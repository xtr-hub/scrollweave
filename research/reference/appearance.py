"""Self-supervised, video-specific overlay appearance prior.

Only learns from cross-scroll *independent witness* labels; no annotation,
semantic UI classes, oracle positions or test ground truth are used.

This is a weak prior, not a segmentation oracle. All estimates are diagnostic
and can be wrong if the page itself resembles the UI's appearance.
"""
from __future__ import annotations
import numpy as np
from scipy import ndimage as ndi


def ui_appearance_probability(
    frames: np.ndarray,
    suspect: np.ndarray,
    document_witness: np.ndarray,
    *,
    bins: int = 16,
    training_indices: np.ndarray | list[int] | None = None,
    smoothing: float = 0.75,
    neighborhood: int = 7,
) -> np.ndarray:
    """Estimate UI posterior based on pseudo-labeled RGB appearance.

    `suspect` marks a source that conflicts with independently corroborated
    page observations at the same document coordinates.
    `document_witness` marks observations that agree with another independent
    scroll position.

    The labels used for learning are *only* the image-derived observations.
    The output is for every input frame (including frames held out of learning).
    """
    arr = np.asarray(frames, dtype=np.uint8)
    suspect = np.asarray(suspect, dtype=bool)
    witness = np.asarray(document_witness, dtype=bool)
    if arr.ndim != 4 or arr.shape[-1] != 3:
        raise ValueError('frames must be N x H x W x 3 RGB')
    if suspect.shape != arr.shape[:-1] or witness.shape != suspect.shape:
        raise ValueError('label shapes must match N x H x W')
    if bins < 4 or bins > 64 or 256 % bins:
        raise ValueError('bins must divide 256, between 4 and 64')
    if neighborhood < 1 or neighborhood % 2 == 0:
        raise ValueError('neighborhood must be odd and positive')
    if training_indices is None:
        train = np.arange(len(arr))
    else:
        train = np.asarray(training_indices, dtype=np.intp)
        if not len(train) or np.any(train < 0) or np.any(train >= len(arr)):
            raise ValueError('training_indices must select valid frames')

    q = (arr.astype(np.uint16) // (256 // bins))
    index = (q[..., 0] * bins + q[..., 1]) * bins + q[..., 2]
    positive = suspect[train] & ~witness[train]
    negative = witness[train] & ~suspect[train]
    positives = int(positive.sum())
    negatives = int(negative.sum())
    if positives < 32 or negatives < 32:
        return np.zeros(arr.shape[:-1], np.float32)

    p = np.bincount(index[train][positive].ravel(), minlength=bins**3)
    n = np.bincount(index[train][negative].ravel(), minlength=bins**3)
    p = ndi.gaussian_filter(p.reshape((bins,) * 3).astype(np.float32), smoothing, mode='nearest')
    n = ndi.gaussian_filter(n.reshape((bins,) * 3).astype(np.float32), smoothing, mode='nearest')
    # Regularization prevents singleton colors from becoming certain UI.
    posterior = (p + 2.0) / (p + n + 40.0)
    prob = posterior.ravel()[index]
    return ndi.uniform_filter(prob, size=(1, neighborhood, neighborhood), mode='nearest').astype(np.float32)
