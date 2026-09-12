"""Validate and sign Shortcuts plists from the shell.

The library does the work; this is the thin end of it, for a build script that
has nothing else to call. A generator written in Python should call
`shortcut_forge_lib.build.build_all()` directly instead.
"""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path

import argcomplete

from shortcut_forge_cli.progress import error, info, success
from shortcut_forge_lib import toolchain


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "examples:\n"
            "  %(prog)s validate dist/*.xml --waive 'Shortcuts Playground prompt text'\n"
            "  %(prog)s sign 'dist/Car Greetings.xml' --mode anyone"
        ),
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('shortcut-forge')}")
    subparsers = parser.add_subparsers(dest="command")
    add_validate_command(subparsers)
    add_sign_command(subparsers)
    argcomplete.autocomplete(parser)
    return parser


def add_validate_command(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "validate",
        help="run the validator and fail on anything not waived",
        formatter_class=Formatter,
    )
    sub.add_argument("xml", type=Path, nargs="+", help="unsigned XML plist(s)")
    sub.add_argument(
        "--waive",
        action="append",
        default=[],
        metavar="PATTERN",
        help="regular expression for an error line that is expected and deliberate; repeatable",
    )
    sub.add_argument("--target-macos", default=toolchain.DEFAULT_TARGET_MACOS, help="macOS version to validate against")
    sub.add_argument("--platform", default=toolchain.DEFAULT_PLATFORM, help="target platform")
    sub.set_defaults(func=handle_validate)


def add_sign_command(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser("sign", help="sign XML plist(s) into .shortcut files", formatter_class=Formatter)
    sub.add_argument("xml", type=Path, nargs="+", help="unsigned XML plist(s)")
    sub.add_argument("--name", help="library name for the signed file; only with a single XML (default: the file stem)")
    sub.add_argument("--mode", default="anyone", choices=["anyone", "people-who-know-me"], help="signing mode")
    sub.add_argument("--output-dir", type=Path, help="where the signed file goes (default: next to the XML)")
    sub.set_defaults(func=handle_sign)


def handle_validate(args: argparse.Namespace) -> int:
    failed = False
    for xml in args.xml:
        report = toolchain.validate(xml, waived=args.waive, target_macos=args.target_macos, platform=args.platform)
        if report.ok:
            waived = len(report.errors)
            success(f"{xml.name}: validated" + (f" ({waived} waived)" if waived else ""))
        else:
            failed = True
            error(f"{xml.name}: FAILED")
            for line in report.unexpected:
                info(f"    {line}")
    return 1 if failed else 0


def handle_sign(args: argparse.Namespace) -> int:
    if args.name and len(args.xml) > 1:
        raise argparse.ArgumentError(None, "--name applies to a single XML file")
    for xml in args.xml:
        signed = toolchain.sign(xml, name=args.name or xml.stem, mode=args.mode, output_dir=args.output_dir)
        success(f"{xml.name}: signed -> {signed}")
    return 0


def main() -> None:
    try:
        parser = build_parser()
        args = parser.parse_args()
        if not hasattr(args, "func"):
            parser.print_help()
            raise SystemExit(2)
        raise SystemExit(args.func(args))
    except KeyboardInterrupt:
        pass
    except argparse.ArgumentError as e:  # post-parse validation: a usage error
        error(str(e))
        raise SystemExit(2) from e
    except (toolchain.ToolNotFoundError, toolchain.SigningError, OSError) as e:  # the CLI boundary
        error(str(e))
        raise SystemExit(1) from e
