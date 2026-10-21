from support import Twin, word


def test_amplifier_writes_a_file_in_the_opencode_project(opencode: Twin) -> None:
    project = opencode.project("coding")

    reply = opencode.opencode_run(
        project,
        "Create a file named notes.txt in the current directory whose only content is the line amplifier-wrote-this "
        "followed by a newline. Then reply with exactly the word done",
    )

    assert word(reply.text) == "done"
    assert opencode.read(f"{project}/notes.txt") == "amplifier-wrote-this\n"
    assert opencode.check("git status --porcelain", workdir=project) == "?? notes.txt\n"
