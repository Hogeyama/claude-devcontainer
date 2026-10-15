{
  description = "Development tools for claude-devcontainer";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in
    {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          playwright-browsers = pkgs.playwright-driver.browsers.override {
            withFirefox = false;
            withWebkit = false;
          };
          # @playwright/cli is only this entry point over playwright-core.
          playwright-cli-js = pkgs.writeText "playwright-cli.js" ''
            require("${pkgs.playwright-driver}/lib/tools/cli-client/program")
              .program({ embedderVersion: "${pkgs.playwright-driver.version}" })
              .catch((e) => { console.error(e.message); process.exit(1); });
          '';
          playwright-cli = pkgs.writeShellApplication {
            name = "playwright-cli";
            runtimeInputs = [ pkgs.nodejs_22 ];
            text = ''
              exec node ${playwright-cli-js} "$@"
            '';
          };
        in
        {
          default = pkgs.mkShellNoCC {
            packages = with pkgs; [
              python3
              nodejs_22
              pnpm
              jdk17_headless
              gradle
              git
              docker-client
              openssl
              shellcheck
              playwright-cli
            ];
            PLAYWRIGHT_BROWSERS_PATH = playwright-browsers;
            PLAYWRIGHT_MCP_BROWSER = "chromium";
            # The agent image has no fonts.
            FONTCONFIG_FILE = pkgs.makeFontsConf {
              fontDirectories = with pkgs; [ dejavu_fonts liberation_ttf noto-fonts-cjk-sans ];
            };
          };
        });
    };
}
