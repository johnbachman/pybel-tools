# -*- coding: utf-8 -*-

import logging

import networkx as nx

from pybel import BELGraph
from pybel.struct.filters import filter_edges, filter_nodes
from pybel.struct.filters.edge_predicate_builders import (
    build_annotation_dict_all_filter, build_annotation_dict_any_filter,
)
from pybel.struct.filters.edge_predicates import is_causal_relation
from .paths import get_nodes_in_all_shortest_paths
from .random_subgraph import get_random_subgraph
from .search import search_node_names
from .. import pipeline
from ..filters.edge_filters import (
    build_author_inclusion_filter, build_edge_data_filter, build_pmid_inclusion_filter,
)
from ..mutation.expansion import (
    expand_all_node_neighborhoods, expand_downstream_causal_subgraph,
    expand_nodes_neighborhoods, expand_upstream_causal_subgraph, get_downstream_causal_subgraph,
    get_upstream_causal_subgraph,
)
from ..mutation.utils import update_node_helper
from ..utils import safe_add_edges

log = logging.getLogger(__name__)

__all__ = [
    'get_subgraph_by_induction',
    'get_subgraph_by_edge_filter',
    'get_subgraph_by_node_filter',
    'get_subgraph_by_neighborhood',
    'get_subgraph_by_second_neighbors',
    'get_subgraph_by_all_shortest_paths',
    'get_subgraph_by_annotation_value',
    'get_subgraph_by_annotations',
    'get_subgraph_by_data',
    'get_subgraph_by_pubmed',
    'get_subgraph_by_authors',
    'get_subgraph_by_node_search',
    'get_causal_subgraph',
    'get_subgraph',
    'get_multi_causal_upstream',
    'get_multi_causal_downstream',
    'get_random_subgraph',
]

#: Induce a subgraph over the given nodes
SEED_TYPE_INDUCTION = 'induction'
#: Induce a subgraph over the given nodes and expand to their first neighbors
SEED_TYPE_NEIGHBORS = 'neighbors'
#: Induce a subgraph over the given nodes and expand to their second neighbors
SEED_TYPE_DOUBLE_NEIGHBORS = 'dneighbors'
#: Induce a subgraph over the nodes in all shortest paths between the given nodes
SEED_TYPE_PATHS = 'shortest_paths'
#: Induce a subgraph over the edges provided by the given authors and their neighboring nodes
SEED_TYPE_AUTHOR = 'authors'
#: Induce a subgraph over the edges provided by the given citations and their neighboring nodes
SEED_TYPE_PUBMED = 'pubmed'
#: Generate an upstream candidate mechanism
SEED_TYPE_UPSTREAM = 'upstream'
#: Generate a downstream candidate mechanism
SEED_TYPE_DOWNSTREAM = 'downstream'
#: Induce a subgraph over the edges matching the given annotations
SEED_TYPE_ANNOTATION = 'annotation'
#: Induce a subgraph over a random set of (hopefully) connected edges
SEED_TYPE_SAMPLE = 'sample'

#: A set of the allowed seed type strings, as defined above
SEED_TYPES = {
    SEED_TYPE_INDUCTION,
    SEED_TYPE_NEIGHBORS,
    SEED_TYPE_DOUBLE_NEIGHBORS,
    SEED_TYPE_PATHS,
    SEED_TYPE_UPSTREAM,
    SEED_TYPE_DOWNSTREAM,
    SEED_TYPE_PUBMED,
    SEED_TYPE_AUTHOR,
    SEED_TYPE_ANNOTATION,
    SEED_TYPE_SAMPLE
}

#: Seed types that don't take node lists as their arguments
NONNODE_SEED_TYPES = {
    SEED_TYPE_ANNOTATION,
    SEED_TYPE_AUTHOR,
    SEED_TYPE_PUBMED,
    SEED_TYPE_SAMPLE,
}


class NodeDegreeIterError(ValueError):
    """Raised when failing to iterate over node degrees"""


@pipeline.mutator
def get_subgraph_by_induction(graph, nodes):
    """Induces a graph over the given nodes. Returns None if none of the nodes are in the given graph.

    :param pybel.BELGraph graph: A BEL graph
    :param iter[tuple] nodes: A list of BEL nodes in the graph
    :rtype: Optional[pybel.BELGraph]
    """
    if all(node not in graph for node in nodes):
        return

    return graph.subgraph(nodes)


@pipeline.mutator
def get_subgraph_by_node_filter(graph, node_filters):
    """Induces a graph on the nodes that pass all filters

    :param pybel.BELGraph graph: A BEL graph
    :param node_filters: A node filter or list/tuple of node filters
    :type node_filters: types.FunctionType or iter[types.FunctionType]
    :return: A subgraph induced over the nodes passing the given filters
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_induction(graph, filter_nodes(graph, node_filters))


@pipeline.mutator
def get_subgraph_by_neighborhood(graph, nodes):
    """Gets a BEL graph around the neighborhoods of the given nodes. Returns none if no nodes are in the graph

    :param pybel.BELGraph graph: A BEL graph
    :param iter[tuple] nodes: An iterable of BEL nodes
    :return: A BEL graph induced around the neighborhoods of the given nodes
    :rtype: Optional[pybel.BELGraph]
    """
    result = BELGraph()

    node_set = set(nodes)

    if all(node not in graph for node in node_set):
        return

    safe_add_edges(result, graph.in_edges_iter(nodes, keys=True, data=True))
    safe_add_edges(result, graph.out_edges_iter(nodes, keys=True, data=True))

    update_node_helper(graph, result)

    return result


@pipeline.mutator
def get_subgraph_by_second_neighbors(graph, nodes, filter_pathologies=False):
    """Gets a BEL graph around the neighborhoods of the given nodes, and expands to the neighborhood of those nodes

    :param pybel.BELGraph graph: A BEL graph
    :param iter[tuple] nodes: An iterable of BEL nodes
    :param bool filter_pathologies: Should expansion take place around pathologies?
    :return: A BEL graph induced around the neighborhoods of the given nodes
    :rtype: Optional[pybel.BELGraph]
    """
    result = get_subgraph_by_neighborhood(graph, nodes)

    if result is None:
        return

    expand_all_node_neighborhoods(graph, result, filter_pathologies=filter_pathologies)
    return result


@pipeline.mutator
def get_subgraph_by_all_shortest_paths(graph, nodes, weight=None, remove_pathologies=True):
    """Induces a subgraph over the nodes in the pairwise shortest paths between all of the nodes in the given list

    :param pybel.BELGraph graph: A BEL graph
    :param set[tuple] nodes: A set of nodes over which to calculate shortest paths
    :param str weight: Edge data key corresponding to the edge weight. If None, performs unweighted search
    :param bool remove_pathologies: Should the pathology nodes be deleted before getting shortest paths?
    :return: A BEL graph induced over the nodes appearing in the shortest paths between the given nodes
    :rtype: Optional[pybel.BELGraph]
    """
    query_nodes = []

    for node in nodes:
        if node not in graph:
            log.debug('%s not in %s', node, graph)
            continue
        query_nodes.append(node)

    if not query_nodes:
        return

    induced_nodes = get_nodes_in_all_shortest_paths(graph, query_nodes, weight=weight,
                                                    remove_pathologies=remove_pathologies)

    if not induced_nodes:
        return

    return get_subgraph_by_induction(graph, induced_nodes)


@pipeline.mutator
def get_subgraph_by_edge_filter(graph, edge_filters):
    """Induces a subgraph on all edges that pass the given filters
    
    :param pybel.BELGraph graph: A BEL graph 
    :param edge_filters: A predicate or list of predicates (graph, node, node, key, data) -> bool
    :type edge_filters: (pybel.BELGraph, tuple, tuple, int) -> bool or list[(pybel.BELGraph, tuple, tuple, int) -> bool]
    :return: A BEL subgraph induced over the edges passing the given filters
    :rtype: pybel.BELGraph
    """
    result = BELGraph()

    safe_add_edges(result, (
        (u, v, k, graph.edge[u][v][k])
        for u, v, k in filter_edges(graph, edge_filters)
    ))

    update_node_helper(graph, result)

    return result


@pipeline.mutator
def get_subgraph_by_data(graph, annotations):
    """Returns the subgraph filtering for Citation, Evidence or Annotation in the edges.
    
    :param pybel.BELGraph graph: A BEL graph
    :param dict annotations: Annotation filters (match all with :func:`pybel.utils.subdict_matches`)
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_edge_filter(graph, build_edge_data_filter(annotations))


@pipeline.mutator
def get_subgraph_by_annotations(graph, annotations, or_=None):
    """Returns the subgraph given an annotations filter.

    :param graph: pybel.BELGraph graph: A BEL graph
    :param dict[str,set[str]] annotations: Annotation filters (match all with :func:`pybel.utils.subdict_matches`)
    :param boolean or_: if True any annotation should be present, if False all annotations should be present in the
                        edge. Defaults to True.
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    edge_filter_builder = (
        build_annotation_dict_any_filter
        if (or_ is None or or_) else build_annotation_dict_all_filter
    )

    return get_subgraph_by_edge_filter(graph, edge_filter_builder(annotations))


@pipeline.mutator
def get_subgraph_by_annotation_value(graph, annotation, value):
    """Builds a new subgraph induced over all edges whose annotations match the given key and value

    :param pybel.BELGraph graph: A BEL graph
    :param str annotation: The annotation to group by
    :param str value: The value for the annotation
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_annotations(graph, {annotation: {value}})


# FIXME update function for reusability
def update_metadata(value, graph):
    """

    :param pybel.BELGraph value:
    :param pybel.BELGraph graph:
    """
    update_node_helper(graph, value)

    value.namespace_url.update(graph.namespace_url)
    value.namespace_pattern.update(graph.namespace_pattern)
    value.annotation_url.update(graph.annotation_url)
    value.annotation_pattern.update(graph.annotation_pattern)
    value.annotation_list.update(graph.annotation_list)


@pipeline.mutator
def get_causal_subgraph(graph):
    """Builds a new subgraph induced over all edges that are causal

    :param pybel.BELGraph graph: A BEL graph
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_edge_filter(graph, is_causal_relation)


@pipeline.mutator
def get_multi_causal_upstream(graph, nbunch):
    """Gets the union of all the 2-level deep causal upstream subgraphs from the nbunch
    
    :param pybel.BELGraph graph: A BEL graph
    :param tuple or list[tuple] nbunch: A BEL node or list of BEL nodes
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    result = get_upstream_causal_subgraph(graph, nbunch)
    expand_upstream_causal_subgraph(graph, result)
    return result


@pipeline.mutator
def get_multi_causal_downstream(graph, nbunch):
    """Gets the union of all of the 2-level deep causal downstream subgraphs from the nbunch

    :param pybel.BELGraph graph: A BEL graph
    :param tuple or list[tuple] nbunch: A BEL node or list of BEL nodes
    :return: A subgraph of the original BEL graph
    :rtype: pybel.BELGraph
    """
    result = get_downstream_causal_subgraph(graph, nbunch)
    expand_downstream_causal_subgraph(graph, result)
    return result


@pipeline.mutator
def get_subgraph_by_node_search(graph, query):
    """Gets a subgraph induced over all nodes matching the query string

    :param pybel.BELGraph graph: A BEL Graph
    :param str or iter[str] query: A query string or iterable of query strings for node names
    :return: A subgraph induced over the original BEL graph
    :rtype: pybel.BELGraph

    Thinly wraps :func:`search_node_names` and :func:`get_subgraph_by_induction`.
    """
    nodes = search_node_names(graph, query)
    return get_subgraph_by_induction(graph, nodes)


@pipeline.mutator
def get_subgraph(graph, seed_method=None, seed_data=None, expand_nodes=None, remove_nodes=None):
    """Runs pipeline query on graph with multiple subgraph filters and expanders.

    Order of Operations:
    
    1. Seeding by given function name and data
    2. Add nodes
    3. Remove nodes

    :param pybel.BELGraph graph: A BEL graph
    :param str seed_method: The name of the get_subgraph_by_* function to use
    :param seed_data: The argument to pass to the get_subgraph function
    :param list[tuple] expand_nodes: Add the neighborhoods around all of these nodes
    :param list[tuple] remove_nodes: Remove these nodes and all of their in/out edges
    :rtype: Optional[pybel.BELGraph]
    """

    # Seed by the given function
    if seed_method == SEED_TYPE_INDUCTION:
        result = get_subgraph_by_induction(graph, seed_data)

    elif seed_method == SEED_TYPE_PATHS:
        result = get_subgraph_by_all_shortest_paths(graph, seed_data)

    elif seed_method == SEED_TYPE_NEIGHBORS:
        result = get_subgraph_by_neighborhood(graph, seed_data)

    elif seed_method == SEED_TYPE_DOUBLE_NEIGHBORS:
        result = get_subgraph_by_second_neighbors(graph, seed_data)

    elif seed_method == SEED_TYPE_UPSTREAM:
        result = get_multi_causal_upstream(graph, seed_data)

    elif seed_method == SEED_TYPE_DOWNSTREAM:
        result = get_multi_causal_downstream(graph, seed_data)

    elif seed_method == SEED_TYPE_PUBMED:
        result = get_subgraph_by_pubmed(graph, seed_data)

    elif seed_method == SEED_TYPE_AUTHOR:
        result = get_subgraph_by_authors(graph, seed_data)

    elif seed_method == SEED_TYPE_ANNOTATION:
        result = get_subgraph_by_annotations(graph, seed_data['annotations'], or_=seed_data.get('or'))

    elif seed_method == SEED_TYPE_SAMPLE:
        result = get_random_subgraph(
            graph,
            number_edges=seed_data.get('number_edges'),
            seed=seed_data.get('seed')
        )

    elif not seed_method:  # Otherwise, don't seed a subgraph
        result = graph.copy()
        log.debug('no seed function - using full network: %s', result.name)

    else:
        raise ValueError('Invalid seed method: {}'.format(seed_method))

    if result is None:
        log.debug('query returned no results')
        return

    log.debug('original graph has (%s nodes / %s edges)', result.number_of_nodes(), result.number_of_edges())

    # Expand around the given nodes
    if expand_nodes:
        expand_nodes_neighborhoods(graph, result, expand_nodes)
        log.debug('graph expanded to (%s nodes / %s edges)', result.number_of_nodes(), result.number_of_edges())

    # Delete the given nodes
    if remove_nodes:
        for node in remove_nodes:
            if node not in result:
                log.debug('%s is not in graph %s', node, graph.name)
                continue
            result.remove_node(node)
        log.debug('graph contracted to (%s nodes / %s edges)', result.number_of_nodes(), result.number_of_edges())

    log.debug(
        'Subgraph coming from %s (seed type) %s (data) contains %d nodes and %d edges',
        seed_method,
        seed_data,
        result.number_of_nodes(),
        result.number_of_edges()
    )

    return result


@pipeline.mutator
def get_subgraph_by_pubmed(graph, pubmed_identifiers):
    """Induces a subgraph over the edges retrieved from the given PubMed identifier(s)

    :param pybel.BELGraph graph: A BEL graph
    :param str or list[str] pubmed_identifiers: A PubMed identifier or list of PubMed identifiers
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_edge_filter(graph, build_pmid_inclusion_filter(pubmed_identifiers))


@pipeline.mutator
def get_subgraph_by_authors(graph, authors):
    """Induces a subgraph over the edges retrieved publications by the given author(s)

    :param pybel.BELGraph graph: A BEL graph
    :param str or list[str] authors: An author or list of authors
    :rtype: pybel.BELGraph
    """
    return get_subgraph_by_edge_filter(graph, build_author_inclusion_filter(authors))


@pipeline.mutator
def get_largest_component(graph):
    """Gets the giant component of a subgraph

    :param pybel.BELGraph graph: A BEL Graph
    :return: The giant component of the graph
    :rtype: pybel.BELGraph
    """
    return max(nx.weakly_connected_component_subgraphs(graph), key=len)
