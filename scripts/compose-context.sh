#!/bin/sh
# Sourced by host commands; scripts names this directory.
compose() {
    if [ -n "$compose_env_file" ]; then
        set -- --env-file "$compose_env_file" "$@"
    fi
    docker compose -p "$projects" -f "$file" "$@"
}

prepare_compose() {
    compose_env_file=
    root=${CLAUDE_DEVCONTAINER_WORKSPACE:-$(cd "$scripts/../../.." && pwd -P)}
    # The host command's environments use the core's own compose file.
    if [ -n "${CLAUDE_DEVCONTAINER_CONFIG_DIR-}" ]; then
        file=$scripts/../host/compose.yaml
    else
        file=$root/.devcontainer/compose.yaml
    fi
    # -a so that down also finds an environment whose containers have stopped.
    projects=$(docker ps -a --filter "label=devcontainer.local_folder=$root" \
        --filter 'label=com.docker.compose.service=agent' \
        --format '{{.Label "com.docker.compose.project"}}' | sort -u)
    case "$projects" in
    '')
        echo "error: no environment of $root found; start it with 'devcontainer up'." >&2
        exit 1 ;;
    *[![:alnum:]_.-]*)
        echo "error: more than one environment of $root found: $projects" >&2
        exit 1 ;;
    esac
    # initializeCommand does not run here; bring .generated/ up to date with
    # claude.toml as it would.
    if python3 "$scripts/settings.py" generate; then
        case "$1" in
        up|create|start|restart)
            python3 "$scripts/settings.py" check-shared-mounts ;;
        esac
        return 0
    fi
    # A setting in the environment or .devcontainer/.env, which generate refuses,
    # would win over the previous .generated/settings.env. So go on only to stop or
    # inspect the environment, without them: --env-file replaces .env.
    case "$1" in
    down|stop|kill|ps|logs)
        echo "warning: the settings are invalid; running $1 with the previous ones." >&2
        for name in $(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import settings; print(*settings.VARIABLES)' \
                "$scripts"); do
            unset "$name"
        done
        compose_env_file=/dev/null ;;
    *)
        echo "error: fix the settings first; only down, stop, kill, ps and logs run with invalid ones." >&2
        exit 1 ;;
    esac
}
