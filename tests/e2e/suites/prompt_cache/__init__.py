"""Prompt-cache suite: an opencode replay must not trip Anthropic's cache breakpoint.

opencode re-POSTs the whole conversation every turn, and OpenAI's wire format encodes an
assistant turn that said nothing as ``"content": null``. amplifier-agent defaults that to
``""``; the Anthropic provider then places prompt-cache breakpoints across the history.
When a breakpoint landed on the resulting empty text block, Anthropic rejected the entire
request:

    cache_control cannot be set for empty text blocks

Fixed upstream by ``_stamps_empty_text_block`` in amplifier-module-provider-anthropic,
which makes the breakpoint walk skip an empty candidate and keep going. This suite is the
regression test for that fix.

It lives here rather than upstream because what broke was the INTERACTION. The ``""``
default is correct on its own terms, the breakpoint placement was correct on its own
terms, and the failure appears only when opencode's actual wire shape passes through
both. That seam is this repo's to guard.
"""

from __future__ import annotations
