from booktrans.harness.resume_selection import coverage,next_logical

def test_completed_composite_covers_members_and_next_is_stable():
    done=coverage({'ordinary.done'},{'group.system':('footer.168.p.0001','body.p.03715')})
    sequence=[('ordinary.done',('ordinary.done',)),('group.system',('footer.168.p.0001','body.p.03715')),('table.014.r.001.c.001.p.003',('table.014.r.001.c.001.p.003',))]
    assert len(done.completed_logical_ids)==2
    assert done.completed_physical_ids >= {'footer.168.p.0001','body.p.03715'}
    assert next_logical(sequence,done)=='table.014.r.001.c.001.p.003'
    assert next_logical(sequence,done)=='table.014.r.001.c.001.p.003'
