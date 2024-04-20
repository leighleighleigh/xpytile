{ 
  pkgs ? import <nixpkgs> {},
  fetchFromGitHub ? pkgs.fetchFromGitHub
}:
pkgs.stdenv.mkDerivation {
  name = "xpytile";

  src = fetchFromGitHub {
    owner = "leighleighleigh";
    repo = "xpytile";
    rev = "main";
    hash = "sha256-rOyslukbFMp84F2LGqs+gkH8TjxQqkf/qqDezdVNyRQ=";
  };

  propagatedBuildInputs = [
    (pkgs.python3.withPackages (ps: with ps; [
      xlib
    ]))
    pkgs.libnotify
  ];
  
  dontBuild = true;

  installPhase = ''
    mkdir -p $out/{bin,etc}
    install -Dm755 ${./xpytile.py} $out/bin/xpytile
    install -Dm755 ${./xpytile-remote-control.py} $out/bin/xpytile-remote-control
    
    # OVERRIDE SETTINGS IN THIS FILE BY CREATING ~/.config/xpytilerc
    cp xpytilerc $out/etc/
  '';
}
