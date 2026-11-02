"""Public upscaler capabilities and strict, provider-specific input validation."""
from __future__ import annotations
import os
from copy import deepcopy

DEFAULT_UPSCALER = 'comfy:remacri-2x'
PROVIDER_PROFILES = {
    DEFAULT_UPSCALER: 'omoide-upscale-v1',
    'comfy:seedvr2-sharp': 'omoide-upscale-seedvr2-v1',
    'hypir:ultra': 'omoide-upscale-hypir-ultra-v1',
    'hypir:balance': 'omoide-upscale-hypir-balance-v1',
}


def hypir_api_key() -> str:
    from app.config import settings
    return os.environ.get('OMOIDE_HYPIR_API_KEY', '').strip() or settings.repairs.hypir.api_key.get_secret_value().strip()


def parameter_specs(provider: str) -> dict:
    if provider == DEFAULT_UPSCALER:
        return {'scale': {'type': 'integer', 'default': 2, 'choices': [2]}}
    if provider == 'comfy:seedvr2-sharp':
        return {'scale': {'type': 'integer', 'default': 2, 'choices': [2, 4]}}
    specs = {
        'scale': {'type': 'integer', 'default': 2, 'min': 1, 'max': 4},
        'long_edge_size': {'type': 'integer', 'default': -1, 'min': -1, 'max': 16384},
        'short_edge_size': {'type': 'integer', 'default': -1, 'min': -1, 'max': 16384},
        'regeneration_level': {'type': 'integer', 'default': 0, 'min': 0, 'max': 5},
        'seed': {'type': 'integer', 'default': 42, 'min': 0, 'max': 9223372036854775807},
        'prompt': {'type': 'string', 'default': '', 'max_length': 2000},
    }
    if provider == 'hypir:ultra':
        specs.update(texture_level={'type':'integer','default':3,'min':1,'max':5}, creativity_level={'type':'integer','default':0,'min':0,'max':5})
    return specs


def normalize_params(provider: str, params: dict) -> dict:
    if provider not in PROVIDER_PROFILES:
        raise ValueError('Unknown upscaler provider')
    specs = parameter_specs(provider)
    if set(params) - set(specs):
        raise ValueError('Unsupported upscaler parameter')
    result = {}
    for name, spec in specs.items():
        value = params.get(name, spec['default'])
        if spec['type'] == 'integer':
            if type(value) is not int or value < spec.get('min', value) or value > spec.get('max', value) or ('choices' in spec and value not in spec['choices']):
                raise ValueError(f'Invalid {name} for this upscaler')
            if name.endswith('_edge_size') and value == 0:
                raise ValueError(f'{name} must be -1 or positive')
        elif not isinstance(value, str) or len(value) > spec['max_length']:
            raise ValueError(f'Invalid {name} for this upscaler')
        result[name] = value
    return result


def list_upscalers(bridge_profiles: set[str], bridge_reason: str | None = None) -> list[dict]:
    from app.config import settings
    labels = ['Remacri · faithful 2×', 'SeedVR2 7B Sharp', 'HYPIR Ultra', 'HYPIR Balance']
    items = []
    for (provider, profile), label in zip(PROVIDER_PROFILES.items(), labels):
        hosted = provider.startswith('hypir:')
        available = settings.repairs.enabled and (bool(hypir_api_key()) if hosted else profile in bridge_profiles)
        reason = None if available else ('Repair backend disabled' if not settings.repairs.enabled else ('HYPIR API key is not configured' if hosted else bridge_reason or 'Bridge profile is unavailable'))
        items.append(dict(id=provider, label=label, kind='hosted' if hosted else 'local', fidelity_note='Faithful restoration' if provider == DEFAULT_UPSCALER else 'Generative restoration; may invent detail', profile=profile, params=deepcopy(parameter_specs(provider)), available=available, reason=reason, warning='Heavy VRAM use; 4× may be expensive. Prefer a fresh ComfyUI process.' if provider == 'comfy:seedvr2-sharp' else None))
    return items
