import inspect
import pydot
from constants import *
from collections import defaultdict
import collections
from pprint import pprint

class Function(object):
    def __init__(self, num_args):
        self.name = None
        self.num_args = num_args
        self.args = []

    def __repr__(self):
        my_name = str(self.name)
        if not isinstance(self.name, basestring) \
            and isinstance(self.name, collections.Iterable):
            my_name = '.'.join(map(str,self.name))
        if self.args:
            return "%s(%s)" %(my_name,','.join(map(str,self.args)))
        else:
            return "%s()" %(my_name)

def write_graph(GLOBALS, globals_key, graphname, suffix, pred_node, pred_edge, prog='dot'):
    basename = GLOBALS['basename']
    graph_nodes = GLOBALS[globals_key]['nodes']
    graph_edges = GLOBALS[globals_key]['edges']
    graph = pydot.Dot(graphname, graph_type='digraph')
    for node in graph_nodes:
        if pred_node(node):
            graph.add_node( graph_nodes[node] )
    for start,end in graph_edges:
        if pred_edge(start, end):
            graph.add_edge( pydot.Edge(start, end) )
    if not graph.get_node_list() and not graph.get_edge_list():
        pass
    elif not graph.get_edge_list():
        graph.write_png('%s_%s.png' % (basename,suffix), prog='neato')
    else:
        graph.write_png('%s_%s.png' % (basename,suffix), prog=prog)

def debug(GLOBALS):
    write_graph(GLOBALS, 'graph_fn_fn', 'function dependency', 'fns',
        lambda node: '.' not in node,
        lambda start,end: '.' not in start.get_name() and '.' not in end.get_name())
    write_graph(GLOBALS, 'graph_fn_cls', 'class dependency', 'cls', lambda _: True, lambda s,e: True)

def fn_fn_find_rightmost(co):
    """ find rightmost occurrence of (CALL_FUNCTION, _) """
    end_index = -1
    for i,x in enumerate(co.code[::-1]):
        opcode, arg = x
        if opcode == byteplay.CALL_FUNCTION:
            end_index = len(co.code) - i
            break
    return end_index

def fn_fn_merge_single_LOAD(bytecode_list, end_index):
    """ transform bytecode - merge into single LOAD_* instr """
    affected_instrs = reserved_slices.keys() + reserved_binary + [byteplay.LOAD_ATTR, byteplay.BUILD_TUPLE]
    for i,(opcode, arg) in enumerate(bytecode_list):
        if opcode not in affected_instrs:
            continue
        the_offset, the_opcode = None, None
        if opcode == byteplay.LOAD_ATTR:
            if i-1 > 0 and bytecode_list[i-1][0] == byteplay.BUILD_MAP:
                pass
            else:
                new_bytecode = (LOAD_OBJ_FN,
                    tuple([v[1] for v in bytecode_list[i-1:i+1]]))
                del bytecode_list[i-1:i+1]
                bytecode_list.insert(i-1, new_bytecode)
                end_index -= 1
            continue
        elif opcode == byteplay.BUILD_TUPLE:
            new_bytecode = (byteplay.BUILD_TUPLE,
                tuple([v[1] for v in bytecode_list[i-2:i]]))
            del bytecode_list[i-2:i]
            bytecode_list.insert(i-2, new_bytecode)
            end_index -= 2
            continue
        if opcode in reserved_slices:
            the_opcode = LOAD_SLICE
            the_offset = reserved_slices[opcode]
            if opcode == byteplay.BUILD_SLICE:
                the_offset = arg
        elif opcode in reserved_binary:
            the_opcode = opcode
            the_offset = 1
        new_bytecode = (the_opcode,
                tuple([v[1] for v in bytecode_list[i-the_offset-1:i]]))
        del bytecode_list[i-the_offset-1:i+1]
        bytecode_list.insert(i-the_offset-1, new_bytecode)
        end_index -= the_offset + 1

def fn_fn_parse(bytecode_list, all_classes):
    func_calls, func_stack = [], []
    for i,(opcode, arg) in enumerate(bytecode_list[::-1]):
        if opcode == byteplay.CALL_FUNCTION:
            if i+1 < len(bytecode_list) and bytecode_list[i+1][0] == byteplay.STORE_MAP:
                continue
            else:
                func_stack.append(Function(arg))
        elif opcode in reserved_loads+reserved_binary+[LOAD_OBJ_FN]:
            if func_stack:
                last_func = func_stack[-1]
                if len(last_func.args) < last_func.num_args:
                    last_func.args.insert(0, arg)
                elif len(last_func.args) == last_func.num_args:
                    last_func.name = arg
                    while len(func_stack) > 1 and last_func.name and len(last_func.args) == last_func.num_args:
                        func_stack[-2].args.insert(0, last_func)
                        func_stack = func_stack[:-1]
                    if len(func_stack) == 1 and func_stack[0].name \
                    and len(func_stack[0].args) == func_stack[0].num_args:
                        # not a class constructor
                        if func_stack[0].name not in all_classes:
                            func_calls.append(func_stack[0])
                        func_stack = []
    return func_calls

def fn_fn_toposort(graph_nodes, graph_edges, all_functions):
    """ assume DAG; perform topological sorting on function nodes """
    # 1. isolated functions (no incoming/outgoing edges)
    isolated_fns = [fn for name,fn in all_functions.iteritems() \
        if name not in graph_nodes]

    # recursive functions do not affect order of testing - convert to DAG
    graph_edges = [(start,end) for start,end in graph_edges if start != end]

    # 2. nodes with only outgoing edges
    L, S = [], []
    for name,fn in all_functions.iteritems():
        if fn not in isolated_fns \
            and not [1 for _,end in graph_edges if end == graph_nodes[name]]:
            S.append((name, fn))
    while S:
        n_name, n_fn = S.pop()
        L.append(n_fn)
        for start, m_node in set(graph_edges):
            if start == graph_nodes[n_name]:
                graph_edges.remove((start, m_node))
                if not [1 for _,end in graph_edges if end == m_node]:
                    S.append((m_node.get_name(),
                        all_functions[m_node.get_name()]))
    assert not graph_edges  # graph is non cyclic
    return isolated_fns, L

def main(GLOBALS):
    all_classes = GLOBALS['all_classes']
    all_functions = GLOBALS['all_functions']
    all_functions_arglen = [
        (f_name, len(inspect.getargspec(f_fn).args)) \
        for f_name,f_fn in all_functions.iteritems()
    ]
    print "[Total: %d functions]" % len(all_functions)
    print "(IIa): function -> function (no classes)"
    print "\t=> work out order to test functions (root -> leaf node)"
    graph_nodes, graph_edges = {}, set()
    for name, fn in all_functions.iteritems():
        co = byteplay.Code.from_code(fn.func_code)

        end_index = fn_fn_find_rightmost(co)
        if end_index == -1: # no function calls in function
            continue

        bytecode_list \
            = [(a,b) for a,b in co.code[:end_index] if a != byteplay.SetLineno]
        fn_fn_merge_single_LOAD(bytecode_list, end_index)
        func_calls = fn_fn_parse(bytecode_list, all_classes)
        # ASSUME <obj>.<fn> calls are valid wrt #args
        called_fns = set()
        invoked_class_methods = defaultdict(set)
        for called_f in func_calls:
            assert isinstance(called_f, Function)

            if not isinstance(called_f.name, basestring):
                continue

            if (called_f.name, called_f.num_args) in all_functions_arglen:
                called_fns.add((called_f.name, called_f.num_args))
                if name not in graph_nodes:
                    graph_nodes[name] = pydot.Node(name)
                if called_f.name not in graph_nodes:
                    graph_nodes[called_f.name] = pydot.Node(called_f.name)
                graph_edges.add((graph_nodes[name], graph_nodes[called_f.name]))
            elif isinstance(called_f.name, tuple):
                classname, methodname = called_f.name
                # assume variable name = class name
                if classname in all_classes:
                    invoked_class_methods[classname].add(methodname)
        if invoked_class_methods:
            GLOBALS['function_class_methods'][name] = invoked_class_methods

    GLOBALS['graph_fn_fn']['nodes'] = graph_nodes
    GLOBALS['graph_fn_fn']['edges'] = set(graph_edges)

    isolated_fns, L = fn_fn_toposort(graph_nodes, graph_edges, all_functions)
    GLOBALS['function_test_order']  = { 'isolated': isolated_fns, 'L': L }

    print "(IIb): class as function input arg/in method body"
    graph_nodes, graph_edges = {}, set()
    for name, fn in all_functions.iteritems():
        co = byteplay.Code.from_code(fn.func_code)
        usage_class = set()
        for opcode, arg in co.code:
            # (LOAD_*, 'classname') in bytecode
            if opcode in reserved_loads \
                and isinstance(arg, basestring) \
                and not arg.startswith('_') \
                and arg in all_classes:
                usage_class.add(all_classes[arg])
        if usage_class:
            for c in usage_class:
                if name not in graph_nodes:
                    graph_nodes[name] = pydot.Node(name)
                if c not in graph_nodes:
                    graph_nodes[c] = pydot.Node(c.__name__+u"\u200B")
                graph_edges.add( (graph_nodes[name], graph_nodes[c]) )
    GLOBALS['graph_fn_cls']['nodes'] = graph_nodes
    GLOBALS['graph_fn_cls']['edges'] = graph_edges
