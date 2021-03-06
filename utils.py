import matplotlib.pyplot as plt
from numba import jit
import numpy as np
from scipy.spatial import cKDTree as KDTree


def imgshow(img, cmap="gray", figsize=(10, 10), title="", show=True):
    plt.figure(figsize=figsize)
    plt.imshow(img, cmap=cmap)
    plt.title(title)
    if show:
        plt.show()


@jit(nopython=True)
def _convolve(g, h):
    # The radius in pixels of the kernel about its central point
    radius = h.shape[0] // 2
    out = np.zeros_like(g)
    h180 = h[::-1, ::-1]
    # Loop over rows: y
    for v in range(radius, g.shape[0] - radius):
        # Loop over columns: x
        for u in range(radius, g.shape[1] - radius):
            # Compute element-wise product of the kernel with the current
            # image chunk, sum the result and set the current pixel to
            # that value
            out[v, u] = np.sum(
                g[v - radius : v + radius + 1, u - radius : u + radius + 1]
                * h180
            )
    return out


def convolve2d(img, kern, boundary_mode, fill=0):
    r = kern.shape[0] // 2
    extended_shape = (img.shape[0] + (2 * r), img.shape[1] + (2 * r))

    if boundary_mode == "valid":
        return _convolve(img, kern)

    conv_input = np.empty(extended_shape)
    if boundary_mode == "fill":
        conv_input[:, :] = fill
        conv_input[r:-r, r:-r] = img
    elif boundary_mode == "extend":
        conv_input[r:-r, r:-r] = img
        # Top
        conv_input[:r, :] = conv_input[r : r + 1, :]
        # Bottom
        conv_input[-r:, :] = conv_input[-r - 1 : -r, :]
        # Left
        conv_input[:, :r] = conv_input[:, r : r + 1]
        # Right
        conv_input[:, -r:] = conv_input[:, -r - 1 : -r]
    elif boundary_mode == "mirror":
        conv_input[r:-r, r:-r] = img
        # Top
        conv_input[:r, :] = np.flip(conv_input[r : r + r, :], 0)
        # Bottom
        conv_input[-r:, :] = np.flip(conv_input[-r - r : -r, :], 0)
        # Left
        conv_input[:, :r] = np.flip(conv_input[:, r : r + r], 1)
        # Right
        conv_input[:, -r:] = np.flip(conv_input[:, -r - r : -r], 1)
    elif boundary_mode == "wrap":
        conv_input[r:-r, r:-r] = img
        # Top
        conv_input[:r, r:-r] = img[-r:, :]
        # Bottom
        conv_input[-r:, r:-r] = img[:r, :]
        # Left
        conv_input[r:-r, :r] = img[:, -r:]
        # Right
        conv_input[r:-r, -r:] = img[:, :r]
        # NW
        conv_input[:r, :r] = img[-r:, -r:]
        # NE
        conv_input[:r, -r:] = img[-r:, :r]
        # SW
        conv_input[-r:, :r] = img[:r, -r:]
        # SE
        conv_input[-r:, -r:] = img[:r, :r]
    else:
        raise ValueError(f"Invalide boundary mode: {boundary_mode}")

    conv_out = _convolve(conv_input, kern)
    return conv_out[r:-r, r:-r].copy()


def get_gaussian_kernel(n, sigma):
    if not n % 2:
        raise ValueError(f"Kernel size must be odd: {n}")

    mean = n // 2
    h = np.empty((n, n))
    for j in range(n):
        for k in range(n):
            h[k, j] = np.exp(
                -((j - mean) ** 2 + (k - mean) ** 2) / (2.0 * sigma ** 2)
            )
    h /= h.sum()
    return h


def gaussian(sigma):
    # 3-sigma rule
    n = int(5 * sigma) + (1 - (sigma % 2))
    return get_gaussian_kernel(n, sigma)


def harris_response(img, conv_mode="valid"):
    w = gaussian(2)
    s_u = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])
    s_v = s_u.T
    Iu = convolve2d(img, s_u, boundary_mode=conv_mode)
    Iv = convolve2d(img, s_v, boundary_mode=conv_mode)
    Iuu = convolve2d(Iu * Iu, w, boundary_mode=conv_mode)
    Ivv = convolve2d(Iv * Iv, w, boundary_mode=conv_mode)
    Iuv = convolve2d(Iu * Iv, w, boundary_mode=conv_mode)

    # Prevent divide by zero
    epsilon = 1e-10
    H = ((Iuu * Ivv) - (Iuv * Iuv)) / (Iuu + Ivv + epsilon)
    return H


def get_maxima(im, threshold, sort=False):
    points = []
    m, n = im.shape
    for k in range(1, m - 1):
        for j in range(1, n - 1):
            if im[k, j] < threshold:
                continue
            p = im[k, j]
            if (
                # Neighbors above
                (p > im[k - 1 : k, j - 1 : j + 2]).all()
                # Below
                and (p > im[k + 1 : k + 2, j - 1 : j + 2]).all()
                # Left and right
                and (p > im[k, j - 1 : j + 2 : 2]).all()
            ):
                points.append((j, k, p))
    if sort:
        points.sort(key=lambda v: v[2], reverse=True)
    return points


def anms(H, n=100, c=0.9, use_thresh=True, zipped=False):
    if use_thresh:
        thresh = H.mean() + H.std()
    else:
        thresh = H.min()
    maxima = get_maxima(H, thresh)
    rmax = np.max(H.shape) ** 2
    anms_maxima = []
    for i, (u, v, h) in enumerate(maxima):
        rmin = rmax
        for k, (uu, vv, hh) in enumerate(maxima):
            d = np.sqrt((u - uu) ** 2 + (v - vv) ** 2)
            if i != k and (h < c * hh) and (d < rmin):
                rmin = d
        anms_maxima.append((u, v, rmin))
    anms_maxima.sort(key=lambda v: v[2], reverse=True)
    return _maxima_to_uv(anms_maxima[:n], zipped)


def anms_kdtree(H, n=100, c=0.9, edge=10, use_thresh=True, zipped=False):
    # Set threshold to filter out weak corners
    H = H[edge:-edge, edge:-edge]
    if use_thresh:
        thresh = H.mean() + H.std()
    else:
        thresh = H.min()
    # Get candidates
    maxima = get_maxima(H, thresh)
    # List for storing distance weighted key point candidates
    # list((u, v, dist_min))
    anms_maxima = []
    # Maxima uv coords
    muv = np.array([mi[:2] for mi in maxima])
    # Maxima harris response values
    mh = np.array([mi[2] for mi in maxima])
    nm = len(maxima)
    # kd-tree that efficiently keeps track of nearest neighbors
    # for each point
    tree = KDTree(muv)
    for i in range(nm):
        u, v = muv[i]
        h = mh[i]
        # Progressively search farther out from current point,
        # one neighbor at a time. Start at 2 to avoid selecting
        # self.
        # This has a worst-case of O(n^2) (result allocation)
        # but in practice that rarely/never happens, as a
        # satisfactory point is usually found after a few iterations.
        for k in range(2, nm):
            dist, ind = tree.query(muv[i : i + 1], k=k)
            # Shapes of dist and ind are (1, nm) so we need
            # to grab index 0 to get data. -1 gives farthest
            # neighbor in query group
            hneighbor = mh[ind[0, -1]]
            # ANMS selection condition
            if h < c * hneighbor:
                anms_maxima.append((u+edge, v+edge, dist[0, -1]))
                break
    anms_maxima.sort(key=lambda v: v[2], reverse=True)
    return _maxima_to_uv(anms_maxima[:n], zipped)


def _maxima_to_uv(maxima, zipped):
    if not zipped:
        u = np.array([mi[0] for mi in maxima])
        v = np.array([mi[1] for mi in maxima])
        return u, v
    return np.array([(mi[0], mi[1]) for mi in maxima])
