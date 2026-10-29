"""Independent-evidence auditing layered on the calibrated compositor.

The existing source selector is intentionally unchanged, so this stage does
not claim to eliminate dynamic UI; callers get explicit uncertainty instead.
"""
from .layered_calibrated import reconstruct_layered as _calibrated
from .evidence import audit_sources


def reconstruct_layered(frames, positions, *args, **kwargs):
    result, debug = _calibrated(frames, positions, *args, **kwargs)
    evidence = audit_sources(frames, positions, result.source_ids)
    debug.update(independent_support=evidence.independent_support,
                 independent_views=evidence.independent_views,
                 conflict_map=evidence.conflict,
                 unverified_map=evidence.unverified,
                 invalid_source_map=evidence.invalid_source)
    return result, debug
