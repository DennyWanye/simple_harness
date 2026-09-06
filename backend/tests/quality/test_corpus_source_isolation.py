"""New source admission path excludes setup from real Host message projection."""
import pytest
from deskpet.quality.corpus_c01 import SETUPS,compile_setup
from deskpet.quality.corpus_source import admit_setup_source
from tests.memory.test_primary_read_api import setup,result,AUTH


@pytest.mark.asyncio
async def test_setup_public_source_not_foreground_history(tmp_path):
    f=await setup(tmp_path)
    batch=compile_setup('C01-01',SETUPS['C01-01'][0],scenario_clock='2026-09-06T10:00:00+08:00')
    pair=await admit_setup_source(path=f.path,subject=AUTH.subject,authority_ref=AUTH.authority_ref,batch=batch)
    assert await admit_setup_source(path=f.path,subject=AUTH.subject,authority_ref=AUTH.authority_ref,batch=batch)==pair
    page=result(await f.send('primary.messages.page',{'primary_ref':f.primary},key='empty-query-history'))
    assert page['items']==[]
    result(await f.send('queue.enqueue',{'text':'actual independent query'},key='query'))
    page=result(await f.send('primary.messages.page',{'primary_ref':f.primary},key='query-history'))
    assert [(item['role'],item['text']) for item in page['items']]==[('user','actual independent query')]
