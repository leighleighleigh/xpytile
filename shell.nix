{ pkgs ? import <nixpkgs> {}}:
pkgs.mkShell rec {
  name = "xpytile";

  buildInputs = with pkgs; [
    (python3.withPackages(ps: with ps; [xlib]))
    libnotify
  ];
}
