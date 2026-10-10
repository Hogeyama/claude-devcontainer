# Owned by this workspace: install the tools the agent needs. It runs as root
# while the agent image is built on the host, so it can reach package
# registries; the agent itself cannot.
# apt-get update
# apt-get install -y --no-install-recommends openjdk-17-jdk-headless gradle
# rm -rf /var/lib/apt/lists/*
