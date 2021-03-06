# -*- coding: utf-8 -*-

import unittest

from pybel import BELGraph
from pybel.constants import *
from pybel_tools.mutation import enrich_pubmed_citations
from tests.constants import ManagerMixin


class TestCitations(ManagerMixin):
    def setUp(self):
        super(TestCitations, self).setUp()

        self.pmid = "9611787"

        g = BELGraph()

        g.add_node(1)
        g.add_node(2)

        g.add_edge(1, 2, attr_dict={
            CITATION: {
                CITATION_TYPE: CITATION_TYPE_PUBMED,
                CITATION_REFERENCE: self.pmid
            }
        })

        self.graph = g

    def test_enrich_overwrite(self):
        citation = self.manager.get_or_create_citation(type=CITATION_TYPE_PUBMED, reference=self.pmid)
        self.manager.session.commit()
        self.assertIsNone(citation.date)
        self.assertIsNone(citation.name)

        enrich_pubmed_citations(self.graph, manager=self.manager)

        _, _, d = self.graph.edges(data=True)[0]
        citation_dict = d[CITATION]

        self.assertIn(CITATION_NAME, citation_dict)

        self.assertIn(CITATION_DATE, citation_dict)
        self.assertEqual('1998-05-01', citation_dict[CITATION_DATE])

        self.assertIn(CITATION_AUTHORS, citation_dict)
        self.assertEqual(
            {'Lewell XQ', 'Judd DB', 'Watson SP', 'Hann MM'},
            set(citation_dict[CITATION_AUTHORS])
        )

    def test_enrich_graph(self):
        enrich_pubmed_citations(self.graph, manager=self.manager)

        _, _, d = self.graph.edges(data=True)[0]
        citation_dict = d[CITATION]

        self.assertIn(CITATION_NAME, citation_dict)

        self.assertIn(CITATION_DATE, citation_dict)
        self.assertEqual('1998-05-01', citation_dict[CITATION_DATE])

        self.assertIn(CITATION_AUTHORS, citation_dict)
        self.assertEqual(
            {'Lewell XQ', 'Judd DB', 'Watson SP', 'Hann MM'},
            set(citation_dict[CITATION_AUTHORS])
        )


if __name__ == '__main__':
    unittest.main()
