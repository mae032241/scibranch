"""Terminal waiting consumes its accepted expanded job without native replay."""
from scidiscovery.research_loop import contracts as c, native, runtime
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


def test_r8_wait_native_seals_supplied_inventory_with_referenced_materials(tmp_path,monkeypatch):
    root,_=direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.bin':'one exact original model'})
    captured=[]
    with monkeypatch.context() as waiting:
        def accept(selected,step):
            inventory=c.accept_current_inventory(selected,step)
            captured.append(inventory)
            return inventory
        waiting.setattr(c,'seal_result',accept)
        job,work,record=finish(root,3,'W',[2],files={'next-model.bin':'one new original model'})
    inventory=captured[0]
    canonical=c.read(root/'jobs/003.json')
    assert inventory.job==job and inventory.job!=canonical
    assert c.job_record(inventory.job)==canonical
    assert inventory.job['direct_materials']
    assert not (record/'result-identity.json').exists()
    originals={path:path.read_bytes() for path in (
        record/'native-intent.json',record/'native-outcome.json',record/'usage.json',record/'science-output.json')}
    # The fixture owns an already stopped terminal receipt. Waiting must seal
    # that receipt and must never submit another native turn.
    monkeypatch.setattr(runtime,'node_state',lambda *args:'terminal')
    def forbidden(*args,**kwargs):
        raise AssertionError('Terminal waiting attempted another native launch')
    monkeypatch.setattr(runtime,'start',forbidden)

    result=native.wait_native(root,3,inventory=inventory)

    assert result['stage_intent']=='decision'
    assert (record/'result-identity.json').is_file()
    assert c.validate(work)['scientific_result']==result['scientific_result']
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert not (record/'continuations').exists()
