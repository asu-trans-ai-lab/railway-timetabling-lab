"""Version identifiers only; soft_lagrangian owns the physical predicate."""
import hashlib
import json

HEADWAY_MODEL_LEGACY_ENTRY = 'LEGACY_ENTRY'
HEADWAY_MODEL_SEGMENT_CLEARANCE_V1 = 'SEGMENT_CLEARANCE_V1'
MODELS = (HEADWAY_MODEL_LEGACY_ENTRY, HEADWAY_MODEL_SEGMENT_CLEARANCE_V1)


def physics_metadata(headway_model, safety_headway):
    if headway_model not in MODELS:
        raise ValueError(f'unknown headway model: {headway_model}')
    values = dict(headway_model=headway_model, safety_headway=float(safety_headway),
                  physics_model_version=('legacy_entry_v1' if headway_model == HEADWAY_MODEL_LEGACY_ENTRY
                                         else 'segment_clearance_v1'))
    return {**values, 'physics_fingerprint': hashlib.sha256(
        json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()}
