# SPDX-FileCopyrightText: 2017 GitHub, Inc.
# SPDX-License-Identifier: MIT
"""The names of the languages of code that GitHub knows, in small letters: each
name and alias without a blank of `lib/linguist/languages.yml` of GitHub Linguist
v9.7.0 (https://github.com/github-linguist/linguist, MIT license, Copyright (c)
2017 GitHub, Inc.), the list by which GitHub names the language of fenced code.

Of the info string of a fence, the engine reads the first word only where it is one
of these (structures.info_cut): GitHub shows none of it, and a word of a fixed list
can't carry a message. A list that is part of the code needs no download while the
assistant runs. To renew it, take languages.yml of the latest release of Linguist,
and write each name and alias that holds no blank, in small letters, sorted and
apart, as here, but the one that names a vendor of an AI too: the package names a
vendor only in its table of engines (tests/test_templates.py), so a fence of that
language loses its name. Then change the version above. The words of DRAWN, which GitHub
draws, are in it, which tests/test_assistant.py checks, and `math`, which Linguist
doesn't name, comes on its own.
"""

# The words, apart by blanks.
LINGUIST_WORDS = """
4d abap abl abnf abuild acfm ackrc aconf actionscript actionscript3 ada ada2005
ada95 adb adblock advpl afdko agda ags ahk aidl aiken al aleo algol alloy altium
amfm ampl amusewiki angelscript antlers antlr apache apacheconf apex apkbuild apl
apples applescript arc arexx as3 asciidoc asl asm asn.1 asp asp.net aspectj aspx
aspx-vb assembly astro asymptote ats ats2 au3 augeas autoconf autohotkey autoit
autoit3 autoitscript awk b b3d b4x ballerina baml bash basic bat batch batchfile
bazel bbcode be beef befunge berry bh bibtex bicep bikeshed bird bird2 bird3 bison
bitbake blade blitz3d blitzbasic blitzmax blitzplus blp blueprint bluespec bmax boo
boogie bplus bqn brainfuck brighterscript brightscript bro browserslist bru bsdmake
bsv buildstream byond bzl c c# c++ c++-objdump c-objdump c2hs c3 cabal caddy
caddyfile cadence cairo cake cakescript cameligo cangjie carbon carto cartocss cask
cds ceylon cfc cfm cfml chapel charity checksum checksums chpl chuck cil circom
cirru clarion clarity clean click clipper clips clojure clue cmake cobol coccinelle
codeowners codeql coffee coffee-script coffeescript coldfusion collada commit conf
conll conll-u conll-x console containerfile cooklang cool coq cperl cpp cpp-objdump
cql creole cron crontab crystal csharp cson csound csound-csd csound-orc csound-sco
css csv cucumber cuda cue curlrc curry cweb cwl cycript cylc cypher cython d
d-objdump d2 d2lang dafny dart daslang dataweave dcl delphi denizenscript desktop
dhall diff dircolors django dlang dm dockerfile dogescript dosbatch dosini dotenv
dpatch dtrace dtrace-script dune dylan e e-mail eagle earthfile earthly eask
easybuild ebnf ec ecl eclipse ecmarkdown ecmarkup ecr edge edgeql editor-config
editorconfig edn eex eiffel ejs elisp elixir elm elvish emacs email emberscript eml
envrc eq erb erlang esdl euphoria f# f* factor fancy fantom faust fb fennel figfont
filterscript firrtl fish flatbuffers flex flix fluent flux formatted forth fortran
foxpro fpp freebasic freemarker frege fsharp fstar ftl fundamental futhark g-code
gaml gams gap gas gdb gdscript gdshader gedcom gemfile.lock gemtext genie genshi
geojson gf gherkin git-ignore gitattributes gitconfig gitignore gitmodules gjs gleam
glsl glyph gn gno gnolang gnuplot go go.mod go.sum go.work go.work.sum golang golo
gosu gotmpl grace gradle graphql groff groovy gsc gsp gtk gtkrc gts hack haml
handlebars haproxy harbour hare hash hashes haskell haxe hbs hcl heex help hip
hiveql hlsl hocon holyc hoon hosts html html+django html+ecr html+eex html+erb
html+jinja html+php html+razor html+ruby htmlbars htmldjango http hurl hxml hy
hylang hyphy i7 ical icalendar idl idris ignore igor igorpro ijm ilasm imba imhex
imhexpatternlanguage imhexpl inc inform7 ini ink inputrc io ioke irc isabelle ispc j
jac jai janet jasmin jass jass2 java javascript javascript+erb jcl jflex jinja jison
jolie jq jruby js json json5 jsonc jsoniq jsonl jsonld jsonnet jsp jte julia just
justfile kak kakounescript kakscript kcl kdl kerboscript keyvalues kframework
kickstart kit koka kotlin krl ksy kusto kvlang labview lambdapi langium lark lasso
lassoscript latex latte lean lean4 leex leo less less-css lex lfe lhaskell lhs
ligolang lilypond limbo liquid liquidsoap lisp litcoffee live-script livescript llvm
lobster logos logtalk lolcode lookml loomscript ls lsl lua luau m m2 m3u m4 m4sugar
m68k macaulay2 macruby mail make makefile mako man man-page manpage markdown marko
markojs mask mathematica matlab max max/msp maxmsp maxscript mbox mcfunction md mdoc
mdsvex mdx mediawiki mercury mermaid meson metal metta mf minid miniscript miniyaml
minizinc mint mirah mlir mma modelica modula-2 modula-3 mojo monkey moocode moonbit
moonscript motoko move mps mql4 mql5 msil mtml muf mumps mupad muse mustache myghty
nanorc nargo nasal nasl nasm ncl ne-on nearley nemerle neon neosnippet nesc netlinx
netlinx+erb netlogo newlisp nextflow nginx nickel nim ninja nit nix nixos njk nl
nmodl node noir npmrc nroff nsis nu nu-script numpy nunjucks nush nushell
nushell-script nvim nwscript oasv2 oasv2-json oasv2-yaml oasv3 oasv3-json oasv3-yaml
oberon obj-c obj-c++ obj-j objc objc++ objdump objective-c objective-c++ objective-j
objectivec objectivec++ objectivej objectpascal objectscript objj ocaml octave odin
odin-lang odinlang omgrofl omnetpp-msg omnetpp-ned oncrpc ooc opa opal opencl
openedge openqasm openrc openscad opentofu opts opy org osascript overpassql overpy
ox oxygene oz p4 pact pan pandoc papyrus parrot pascal pasm pawn pcbnew pddl peg.js
pep8 perl perl-6 perl6 php pic pickle picolisp piglatin pikchr pike pir pkg-config
pkgconf pkl plantuml plpgsql plsql pod pogoscript polar pony portugol posh postcss
postscr postscript pot pov-ray povray powerbuilder powerquery powershell praat
prisma pro*c processing procfile progress proguard prolog promela proto protobuf pug
puppet purebasic purescript pwsh py py3 pycon pyret pyrex python python3 q q# qb
qb64 qbasic ql qmake qml qsharp quake quakec quickbasic quint r racket ragel
ragel-rb ragel-ruby rake raku raml rascal rascript raw razor rb rbs rbx rdoc
readline realbasic reason reasonligo rebol red red/system redcode redirects
redscript regex regexp ren'py renderscript renpy rescript restructuredtext rexx rez
rhai rhtml ring riot rmarkdown robotframework robots robotx.txt roc rocq roff ron
rosmsg rouge rpc rpcgen rpgle rs rs-274x rscript rss rst ruby runoff rust rusthon
sage sail salt saltstack saltstate sarif sas sass scala scaml scenic scheme scilab
scss sdc sed self sepolicy sfv sh shaderlab shell shell-script shellcheckrc
shellsession shen sieve singularity sip slang slash slice slim slint smali smalltalk
smarty smithy sml smpl smt snakefile snakemake snipmate snippet solidity soong
sourcemod sourcepawn soy sparql specfile splus sqf sql sqlpl sqlrpgle squeak
squirrel ssh_config sshconfig sshd_config sshdconfig stan star starlark stata stl
stla ston stringtemplate stylus sugarss sum sums supercollider surql surrealql
svelte svg sway sweave swift swig systemverilog tact talon tape tcl tcsh tea teal
templ terra terraform tex texinfo text textgrid textile thrift tl tl-verilog tla
tm-properties tmdl toit tolk toml topojson torrc troff ts tsp tsq tsql tsv tsx
turing turtle twig txl typ typescript typescriptreact typespec typst ucode udiff
ultisnip ultisnips uno unrealscript ur ur/web urweb v vala vb.net vb6 vba vbnet
vbscript vcard vcl vdf velocity vento verilog verse vespa vhdl vhs vim vimhelp viml
vimscript vlang volt vtl vtt vue vyper wasm wast wdl webassembly webidl webvtt
wgetrc wgsl whiley wiki wikitext winbatch wisp wit wl wolfram wollok wren wrenlang
wsdl x10 xbase xbm xc xcompose xdc xdr xhtml xmake xml xml+genshi xml+kid xojo xonsh
xpages xpm xproc xquery xs xsd xsl xslt xten xtend yacc yaml yang yara yas yasnippet
yml yul zap zeek zenscript zephir zig zil zimpl zmodel zsh
"""
CODE_LANGUAGES = frozenset(LINGUIST_WORDS.split()) | {"math"}
