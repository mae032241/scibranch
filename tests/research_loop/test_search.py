"""Small explicit scientific data constructors shared by schema fixtures."""


def plan(label='Mechanism', existing_ref=None, action='screen', other=False):
    rows = [dict(label=label, minimal_test='A bounded discriminating measurement', existing_ref=existing_ref)]
    if other:
        rows.append(dict(label='Unexplored mechanism', minimal_test='Independent negative control', existing_ref=None))
    return dict(scope='Original scientific H scope', commitments=rows, focus=dict(index=1, action=action))


def focus(ref='h001.m1', action='improve'):
    return dict(mechanism_ref=ref, action=action)
