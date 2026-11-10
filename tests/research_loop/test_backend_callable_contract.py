"""Class-level provider contract only; no native execution or qualification."""
import inspect

import pytest

from scidiscovery.research_loop import backends, native


class IndependentClassProvider(backends.Backend):
    # This intentionally remains abstract: these consumers resolve a provider
    # class and need neither an invocation nor invented implementations of its
    # unrelated instance hooks.
    name = 'independent_class_contract'

    @staticmethod
    def response_guidance(schema):
        return '\nIndependent fields: ' + ','.join(schema['required'])

    @staticmethod
    def compose_instructions(prompt, instruction, transport):
        if transport == 'prompt':
            return instruction + '\nIndependent prompt: ' + prompt
        if transport == 'developer':
            return 'Independent prompt: ' + prompt
        raise ValueError('Unsupported independent instruction transport')

    @staticmethod
    def authorship_scope(binding):
        return binding.get('participant_scope') == 'independent_class_contract_v1'

    @staticmethod
    def authorship_proof_kind(binding):
        if not IndependentClassProvider.authorship_scope(binding):
            raise ValueError('Unsupported independent participant scope')
        return 'independent_class_proof_v1'

    @staticmethod
    def validate_participant_event(event, outcome, intent, workspace):
        if (not IndependentClassProvider.authorship_scope(outcome['native_identity'])
                or event != dict(session=intent['session'], workspace=str(workspace))):
            raise ValueError('Independent participant event changed')
        return ('independent_class_session', event['session'])


@pytest.fixture
def declared_provider(monkeypatch):
    # Verify the implementation against the declared callable contract as well
    # as exercising dispatch below. Concrete providers' staticmethods otherwise
    # conceal a stray self parameter in the ABC from every runtime consumer.
    for method in ('response_guidance', 'compose_instructions', 'authorship_scope',
            'authorship_proof_kind', 'validate_participant_event'):
        declaration = backends.Backend.__dict__[method]
        assert isinstance(declaration, staticmethod)
        assert declaration.__isabstractmethod__
        assert inspect.signature(getattr(backends.Backend, method)) == inspect.signature(
            getattr(IndependentClassProvider, method))
    monkeypatch.setattr(backends, '_PROVIDERS', {
        IndependentClassProvider.name: IndependentClassProvider,
    })
    return IndependentClassProvider


@pytest.mark.parametrize('transport', ['prompt', 'developer'])
def test_request_renderer_dispatches_the_declared_class_contract(declared_provider, transport):
    job = dict(role='H', native_role_instruction='Keep the exact independent scope')
    prompt, instruction, schema = native.render_scientific_request(
        job, {}, {}, backend_name=declared_provider.name, instruction_transport=transport)

    assert backends.renderer(declared_provider.name) is declared_provider
    assert instruction.startswith(job['native_role_instruction'] + '\n')
    assert instruction.endswith('\nIndependent fields: ' + ','.join(schema['required']))
    assert 'Independent prompt: Read science-task.json/inputs;' in prompt
    if transport == 'prompt':
        assert prompt.startswith(instruction + '\nIndependent prompt: ')
    else:
        assert job['native_role_instruction'] not in prompt


def test_participant_resolver_dispatches_the_declared_class_contract(declared_provider, tmp_path):
    binding = dict(participant_scope='independent_class_contract_v1')
    provider = backends.participant_provider(binding)
    intent = dict(session='retained-independent-session')
    event = dict(session=intent['session'], workspace=str(tmp_path))
    outcome = dict(native_identity=binding)

    assert provider is declared_provider
    assert provider.authorship_proof_kind(binding) == 'independent_class_proof_v1'
    assert provider.validate_participant_event(event, outcome, intent, tmp_path) == (
        'independent_class_session', intent['session'])
    with pytest.raises(ValueError, match='event changed'):
        provider.validate_participant_event(dict(event, session='other-session'), outcome, intent, tmp_path)
    with pytest.raises(ValueError, match='identity unsupported'):
        backends.participant_provider(dict(binding, participant_scope='unsupported'))
