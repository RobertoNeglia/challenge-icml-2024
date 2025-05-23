import numpy as np
import torch
import torch_geometric
from networkx import from_numpy_array, minimum_spanning_tree
from sklearn.metrics import pairwise_distances
from sklearn.mixture import GaussianMixture

from modules.transforms.liftings.pointcloud2hypergraph.base import (
    PointCloud2HypergraphLifting,
)


class MoGMSTLifting(PointCloud2HypergraphLifting):
    def __init__(
        self, min_components=None, max_components=None, random_state=None, **kwargs
    ):
        super().__init__(**kwargs)
        if min_components is not None:
            assert (
                min_components > 0
            ), "Minimum number of components should be at least 1"
        if max_components is not None:
            assert (
                max_components > 0
            ), "Maximum number of components should be at least 1"
        if min_components is not None and max_components is not None:
            assert min_components <= max_components, (
                "Minimum number of components must be lower or equal to the"
                " maximum number of components."
            )
        self.min_components = min_components
        self.max_components = max_components
        self.random_state = random_state

    def lift_topology(self, data: torch_geometric.data.Data) -> dict:
        # Find a mix of Gaussians
        labels, num_components, means = self.find_mog(data.pos.numpy())

        # Create MST
        distance_matrix = pairwise_distances(means)
        original_graph = from_numpy_array(distance_matrix)
        mst = minimum_spanning_tree(original_graph)

        # Create hypergraph incidence
        number_of_points = data.pos.shape[0]
        incidence = torch.zeros((number_of_points, 2 * num_components))

        # Add to which Gaussian the points belong to
        nodes = torch.arange(0, number_of_points, dtype=torch.int32)
        lbls = torch.tensor(labels, dtype=torch.int32)
        values = torch.ones(number_of_points)
        incidence[nodes, lbls] = values

        # Add neighbours in MST
        for i, j in mst.edges():
            mask_i = labels == i
            mask_j = labels == j
            incidence[mask_i, num_components + j] = 1
            incidence[mask_j, num_components + i] = 1

        incidence = incidence.clone().detach().to_sparse_coo()
        return {
            "incidence_hyperedges": incidence,
            "num_hyperedges": 2 * num_components,
            "x_0": data.x,
        }

    def find_mog(self, data) -> tuple[np.ndarray, int, np.ndarray]:
        if self.min_components is not None and self.max_components is not None:
            possible_num_components = range(
                self.min_components, self.max_components + 1
            )
        elif self.min_components is None and self.max_components is None:
            possible_num_components = [
                2**i for i in range(1, int(np.log2(data.shape[0] / 2)) + 1)
            ]
        else:
            if self.min_components is not None:
                num_components = self.min_components
            elif self.max_components is not None:
                num_components = self.max_components
            else:
                # Cannot happen
                num_components = 1

            gm = GaussianMixture(
                n_components=num_components, random_state=self.random_state
            )
            labels = gm.fit_predict(data)
            return labels, num_components, gm.means_

        best_score = float("inf")
        best_labels = None
        best_num_components = 0
        means = None
        for i in possible_num_components:
            gm = GaussianMixture(n_components=i, random_state=self.random_state)
            labels = gm.fit_predict(data)
            score = gm.aic(data)
            if score < best_score:
                best_score = score
                best_labels = labels
                best_num_components = i
                means = gm.means_
        return best_labels, best_num_components, means
