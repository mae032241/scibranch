"""Small exact ordered-replacement cases; no scientific execution."""
from contextlib import contextmanager
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import atomic_files, experiments as e
from scidiscovery.research_loop import scientific_log_projection as logs, storage_policy as storage


@pytest.mark.parametrize('raw,pairs',[
    (b'prefix /long/source suffix /long/source', [(b'/long/source', b'/science')]),
    (b'abababa', [(b'aba', b'ababa')]),
    (b'abcabc', [(b'abc', b'xy'), (b'yx', b'next')]),
    (b'one-one', [(b'one', b'two'), (b'two-two', b'done')]),
    (b'aab', [(b'aa', b'a'), (b'ab', b'')]),
    (b'partial /sour', [(b'/source', b'complete')]),
    (b'', [(b'empty', b'replacement')]),
    (b'unchanged', []),
])
def test_ordered_projection_matches_whole_bytes_replace_across_small_chunks(raw,pairs):
    expected=raw
    for source,destination in pairs:
        expected=expected.replace(source,destination)
    for size in (1,3,7):
        chunks=(raw[offset:offset+size] for offset in range(0,len(raw),size))
        result=list(logs.replace_blocks(chunks,pairs,context=storage.Deadline(10),block_size=size))
        assert all(len(block)<=size for block in result)
        assert b''.join(result)==expected


def test_many_stages_use_no_recursive_chain_and_split_expanding_destinations():
    pairs=[(f'absent-{number}'.encode(),b'ignored') for number in range(1100)]
    pairs.extend([(b'x',b'long destination'),(b'destination',b'done')])
    blocks=list(logs.replace_blocks([b'x'],pairs,context=storage.Deadline(10),block_size=2))
    assert b''.join(blocks)==b'long done'
    assert all(len(block)<=2 for block in blocks)


def test_replacement_order_uses_string_lengths_and_preserves_equal_length_insertion():
    pairs=logs.encode_replacements({'é':'ab','ab':'done'})
    assert pairs==((b'ab',b'done'),(os.fsencode('é'),b'ab'))
    assert b''.join(logs.replace_blocks([os.fsencode('é')],pairs,
        context=storage.Deadline(10),block_size=4))==b'ab'
    pairs=logs.encode_replacements({'a':'b','b':'c'})
    assert b''.join(logs.replace_blocks([b'a'],pairs,
        context=storage.Deadline(10),block_size=4))==b'c'


def test_raw_projection_reads_only_bounded_blocks_and_rechecks_identity(tmp_path,monkeypatch):
    raw=b'prefix /private/source suffix'*8
    path=tmp_path/'stdout.raw';path.write_bytes(raw)
    identity=dict(size_bytes=len(raw))
    regular=atomic_files._regular_stream
    reads=[]

    class Bounded:
        def __init__(self,stream):self.stream=stream
        def fileno(self):return self.stream.fileno()
        def read(self,size):
            assert 0<size<=7
            reads.append(size)
            return self.stream.read(size)

    @contextmanager
    def bounded(selected):
        with regular(selected) as stream:yield Bounded(stream)

    monkeypatch.setattr(atomic_files,'_regular_stream',bounded)
    monkeypatch.setattr(Path,'read_bytes',lambda self:pytest.fail('Projection read the whole original'))
    options=dict(replacements=[(b'/private/source',b'/science')],context=storage.Deadline(10),block_size=7)
    projected=b''.join(logs.project(path,**identity,**options))
    assert projected==raw.replace(b'/private/source',b'/science') and len(reads)>1
    path.write_bytes(b'changed')
    with pytest.raises(e.IntegrityError):
        list(logs.project(path,**identity,**options))


def test_expansion_then_contraction_uses_final_file_policy_and_shared_total():
    context=storage.Deadline(10)
    inventory=storage.Inventory(dict(enabled=True,max_file_bytes=2,max_total_bytes=3),context=context)
    def projected():
        return logs.replace_blocks([b'x'],[(b'x',b'a'*32),(b'a'*32,b'zz')],
                                   context=context,block_size=3)
    assert logs.measure(projected(),context=context,inventory=inventory)['size_bytes']==2
    assert inventory.files==1 and inventory.total==2
    with pytest.raises(storage.StorageQuotaExceeded,match='max_total_bytes'):
        logs.measure(projected(),context=context,inventory=inventory)


def test_deadline_is_checked_even_when_all_output_is_deleted():
    class Deadline:
        checks=0
        def remaining_seconds(self):
            self.checks+=1
            if self.checks==4:raise TimeoutError('original deadline')
            return 1
    context=Deadline()
    with pytest.raises(TimeoutError,match='original deadline'):
        list(logs.replace_blocks([b'aaaa'],[(b'a',b'')],context=context,block_size=4))
    assert context.checks==4
