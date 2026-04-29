from __future__ import annotations


def test_enqueue_and_drain_local_command(monkeypatch, tmp_path):
    from jobpulse import local_command_inbox

    monkeypatch.setattr(local_command_inbox, "INBOX_DIR", tmp_path)

    command_id = local_command_inbox.enqueue_local_command("apply 1", source="test")
    commands = local_command_inbox.drain_local_commands()

    assert len(commands) == 1
    assert commands[0]["id"] == command_id
    assert commands[0]["text"] == "apply 1"
    assert commands[0]["source"] == "test"
    assert local_command_inbox.drain_local_commands() == []
