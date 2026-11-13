"""/help has to fit in a Discord embed, and name the commands people looked for."""
import desk_help as H

WHO = dict(role="Election Junkie", board="#decision-desk-board", desk="#decision-desk", need=3)


def pages(admin: bool):
    return {key: H.section(key, junkie=True, admin=admin, **WHO) for key, _label, _blurb in H.sections_for(admin)}


def test_every_section_fits_in_an_embed():
    for admin in (False, True):
        for key, (title, body) in pages(admin).items():
            assert len(title) <= 256, key
            assert len(body) <= 4096, f"{key} is {len(body)} characters"


def test_the_commands_from_2026s_wish_list_are_explained():
    text = "\n".join(body for _title, body in pages(admin=True).values())
    for command in ("/forcecall", "/realcall", "/needs", "/closeout", "/final race"):
        assert command in text, command
