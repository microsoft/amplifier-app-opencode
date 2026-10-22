FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tmux less locales \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TERM=xterm-256color

RUN useradd -m -s /bin/bash user
USER user
WORKDIR /home/user

# uv and opencode the way their install pages say. The installers put ~/.local/bin and ~/.opencode/bin on PATH in
# ~/.bashrc, which only interactive shells read; `exec` runs a login shell, so the image sets PATH too.
RUN curl -LsSf https://astral.sh/uv/0.12.2/install.sh | sh
RUN curl -fsSL https://opencode.ai/v2/install | bash -s -- --version 2.1.2
ENV PATH="/home/user/.opencode/bin:/home/user/.local/bin:${PATH}"

RUN git config --global user.name "Twin User" && git config --global user.email user@example.com \
    && mkdir -p /home/user/project
