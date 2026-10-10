# Claude Code用Dev Containerのコア

このディレクトリはgit subtreeで取り込まれたものです。編集せず、カスタマイズは`.devcontainer/`直下の`devcontainer.json`、`compose.yaml`、`claude.env`、`agent-setup.sh`で行ってください。`template/`はそれらの雛形です。

上流の修正は次のコマンドで取り込みます。使い方の詳細は[上流](./UPSTREAM)のREADMEを参照してください。

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
```
