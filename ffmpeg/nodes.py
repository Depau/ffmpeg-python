from __future__ import unicode_literals

from collections import OrderedDict

from .dag import KwargReprNode
from ._utils import escape_chars, get_hash_int
from builtins import object
import os


def _is_of_types(obj, types):
    valid = False
    for stream_type in types:
        if isinstance(obj, stream_type):
            valid = True
            break
    return valid


def _get_types_str(types):
    return ', '.join(['{}.{}'.format(x.__module__, x.__name__) for x in types])


class Stream(object):
    """Represents the outgoing edge of an upstream node; may be used to create more downstream nodes."""

    def __init__(self, upstream_node, upstream_label, node_types, upstream_selector=None):
        if not _is_of_types(upstream_node, node_types):
            raise TypeError('Expected upstream node to be of one of the following type(s): {}; got {}'.format(
                _get_types_str(node_types), type(upstream_node)))
        self.node = upstream_node
        self.label = upstream_label
        self.selector = upstream_selector

    def __hash__(self):
        return get_hash_int([hash(self.node), hash(self.label)])

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __repr__(self):
        node_repr = self.node.long_repr(include_hash=False)
        selector = ""
        if self.selector:
            selector = ":{}".format(self.selector)
        out = '{}[{!r}{}] <{}>'.format(node_repr, self.label, selector, self.node.short_hash)
        return out

    def __getitem__(self, item):
        """
        Select a component of the stream. `stream[:X]` is analogous to `stream.node.stream(select=X)`.
        Please note that this can only be used to select a substream that already exist. If you want to split
        the stream, use the `split` filter.
        """
        if not isinstance(item, slice) or item.start is not None:
            raise ValueError("Invalid syntax. Use 'stream[:\"something\"]', not 'stream[\"something\"]'.")

        return self.node.stream(select=item.stop)


def get_stream_map(stream_spec):
    if stream_spec is None:
        stream_map = {}
    elif isinstance(stream_spec, Stream):
        stream_map = {None: stream_spec}
    elif isinstance(stream_spec, (list, tuple)):
        stream_map = OrderedDict(enumerate(stream_spec))
    elif isinstance(stream_spec, dict):
        stream_map = stream_spec
    return stream_map


def get_stream_map_nodes(stream_map):
    nodes = []
    for stream in list(stream_map.values()):
        if not isinstance(stream, Stream):
            raise TypeError('Expected Stream; got {}'.format(type(stream)))
        nodes.append(stream.node)
    return nodes


def get_stream_spec_nodes(stream_spec):
    stream_map = get_stream_map(stream_spec)
    return get_stream_map_nodes(stream_map)


class Node(KwargReprNode):
    """Node base"""

    @classmethod
    def __check_input_len(cls, stream_map, min_inputs, max_inputs):
        if min_inputs is not None and len(stream_map) < min_inputs:
            raise ValueError('Expected at least {} input stream(s); got {}'.format(min_inputs, len(stream_map)))
        elif max_inputs is not None and len(stream_map) > max_inputs:
            raise ValueError('Expected at most {} input stream(s); got {}'.format(max_inputs, len(stream_map)))

    @classmethod
    def __check_input_types(cls, stream_map, incoming_stream_types):
        for stream in list(stream_map.values()):
            if not _is_of_types(stream, incoming_stream_types):
                raise TypeError('Expected incoming stream(s) to be of one of the following types: {}; got {}'
                                .format(_get_types_str(incoming_stream_types), type(stream)))

    @classmethod
    def __get_incoming_edge_map(cls, stream_map):
        incoming_edge_map = OrderedDict()
        for downstream_label, upstream in list(stream_map.items()):
            incoming_edge_map[downstream_label] = (upstream.node, upstream.label, upstream.selector)
        return incoming_edge_map

    def __init__(self, stream_spec, name, incoming_stream_types, outgoing_stream_type, min_inputs, max_inputs, args=[],
                 kwargs={}):
        stream_map = get_stream_map(stream_spec)
        self.__check_input_len(stream_map, min_inputs, max_inputs)
        self.__check_input_types(stream_map, incoming_stream_types)
        incoming_edge_map = self.__get_incoming_edge_map(stream_map)
        super(Node, self).__init__(incoming_edge_map, name, args, kwargs)
        self.__outgoing_stream_type = outgoing_stream_type

        self.__incoming_stream_types = incoming_stream_types
        self.__min_inputs = min_inputs
        self.__max_inputs = max_inputs

    def stream(self, label=None, select=None):
        """Create an outgoing stream originating from this node.

        More nodes may be attached onto the outgoing stream.
        """
        return self.__outgoing_stream_type(self, label, upstream_selector=select)

    def __getitem__(self, item):
        """Create an outgoing stream originating from this node; syntactic sugar for ``self.stream(label)``.
        It can also be used to apply a selector: e.g. node[0:"audio"] returns a stream with label 0 and
        selector "audio", which is the same as ``node.stream(label=0, select="audio")``.
        """
        if isinstance(item, slice):
            return self.stream(label=item.start, select=item.stop)
        else:
            return self.stream(label=item)

    def _add_streams(self, stream_spec):
        """Attach additional streams after the Node is initialized.
        """
        # Back up previous edges
        prev_edges = self.incoming_edge_map.values()

        # Check new edges
        new_stream_map = get_stream_map(stream_spec)
        self.__check_input_types(new_stream_map, self.__incoming_stream_types)

        # Generate new edge map
        new_inc_edge_map = self.__get_incoming_edge_map(new_stream_map)
        new_edges = new_inc_edge_map.values()

        # Rename all edges
        new_edge_map = dict(enumerate(list(prev_edges) + list(new_edges)))

        # Check new length
        self.__check_input_len(new_edge_map, self.__min_inputs, self.__max_inputs)

        # Overwrite old map (exploiting the fact that dict is mutable; incoming_edge_map is a read-only property)
        if None in self.incoming_edge_map:
            self.incoming_edge_map.pop(None)
        self.incoming_edge_map.update(new_edge_map)


class FilterableStream(Stream):
    def __init__(self, upstream_node, upstream_label, upstream_selector=None):
        super(FilterableStream, self).__init__(upstream_node, upstream_label, {InputNode, FilterNode, SourceNode}, upstream_selector=upstream_selector)


class InputNode(Node):
    """InputNode type"""

    def __init__(self, name, args=[], kwargs={}):
        super(InputNode, self).__init__(
            stream_spec=None,
            name=name,
            incoming_stream_types={},
            outgoing_stream_type=FilterableStream,
            min_inputs=0,
            max_inputs=0,
            args=args,
            kwargs=kwargs
        )

    @property
    def short_repr(self):
        return os.path.basename(self.kwargs['filename'])


class FilterableNode(Node):
    """FilterableNode"""
    def _get_filter(self, outgoing_edges):
        args = self.args
        kwargs = self.kwargs
        if self.name in ('split', 'asplit'):
            args = [len(outgoing_edges)]

        out_args = [escape_chars(x, '\\\'=:') for x in args]
        out_kwargs = {}
        for k, v in list(kwargs.items()):
            k = escape_chars(k, '\\\'=:')
            v = escape_chars(v, '\\\'=:')
            out_kwargs[k] = v

        arg_params = [escape_chars(v, '\\\'=:') for v in out_args]
        kwarg_params = ['{}={}'.format(k, out_kwargs[k]) for k in sorted(out_kwargs)]
        params = arg_params + kwarg_params

        params_text = escape_chars(self.name, '\\\'=:')

        if params:
            params_text += '={}'.format(':'.join(params))
        return escape_chars(params_text, '\\\'[],;')


class FilterNode(FilterableNode):
    def __init__(self, stream_spec, name, max_inputs=1, args=[], kwargs={}):
        super(FilterNode, self).__init__(
            stream_spec=stream_spec,
            name=name,
            incoming_stream_types={FilterableStream},
            outgoing_stream_type=FilterableStream,
            min_inputs=1,
            max_inputs=max_inputs,
            args=args,
            kwargs=kwargs
        )


class SourceNode(FilterableNode):
    def __init__(self, name, args=[], kwargs={}):
        super(SourceNode, self).__init__(
            stream_spec=None,
            name=name,
            incoming_stream_types={},
            outgoing_stream_type=FilterableStream,
            min_inputs=0,
            max_inputs=0,
            args=args,
            kwargs=kwargs
        )


class OutputNode(Node):
    def __init__(self, stream, name, args=[], kwargs={}):
        super(OutputNode, self).__init__(
            stream_spec=stream,
            name=name,
            incoming_stream_types={FilterableStream},
            outgoing_stream_type=OutputStream,
            min_inputs=0,  # Allow streams to be mapped afterwards
            max_inputs=None,
            args=args,
            kwargs=kwargs
        )

    @property
    def short_repr(self):
        return os.path.basename(self.kwargs['filename'])


class OutputStream(Stream):
    def __init__(self, upstream_node, upstream_label, upstream_selector=None):
        super(OutputStream, self).__init__(upstream_node, upstream_label, {OutputNode, GlobalNode, MergeOutputsNode},
                                           upstream_selector=upstream_selector)


class MergeOutputsNode(Node):
    def __init__(self, streams, name):
        super(MergeOutputsNode, self).__init__(
            stream_spec=streams,
            name=name,
            incoming_stream_types={OutputStream},
            outgoing_stream_type=OutputStream,
            min_inputs=1,
            max_inputs=None
        )


class GlobalNode(Node):
    def __init__(self, stream, name, args=[], kwargs={}):
        super(GlobalNode, self).__init__(
            stream_spec=stream,
            name=name,
            incoming_stream_types={OutputStream},
            outgoing_stream_type=OutputStream,
            min_inputs=1,
            max_inputs=1,
            args=args,
            kwargs=kwargs
        )


def stream_operator(stream_classes={Stream}, name=None):
    def decorator(func):
        func_name = name or func.__name__
        [setattr(stream_class, func_name, func) for stream_class in stream_classes]
        return func

    return decorator


def filter_operator(name=None):
    return stream_operator(stream_classes={FilterableStream}, name=name)


def output_operator(name=None):
    return stream_operator(stream_classes={OutputStream}, name=name)
