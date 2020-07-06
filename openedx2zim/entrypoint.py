#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import argparse

from .constants import NAME, SCRAPER, getLogger, setDebug


def main():
    parser = argparse.ArgumentParser(
        prog=NAME, description="Scraper to create ZIM files MOOCs on openedx instances",
    )

    parser.add_argument(
        "--course-url", help="URL of the course you wnat to scrape", required=True,
    )

    parser.add_argument(
        "--email",
        help="Your registered e-mail ID on the platform. Used for authentication",
        required=True,
    )

    parser.add_argument(
        "--password",
        help="The password to your registered account on the platform. If you don't provide one here, you'll be asked for it later",
    )

    parser.add_argument(
        "--name",
        help="ZIM name. Used as identifier and filename (date will be appended)",
        required=True,
    )

    parser.add_argument(
        "--title", help="Custom title for your ZIM. Based on MOOC otherwise.",
    )

    parser.add_argument(
        "--description",
        help="Custom description for your ZIM. Based on MOOC otherwise.",
    )

    parser.add_argument("--creator", help="Name of content creator", default="edX")

    parser.add_argument(
        "--publisher", help="Custom publisher name (ZIM metadata)", default="Kiwix"
    )

    parser.add_argument(
        "--tags",
        help="List of comma-separated Tags for the ZIM file. category:openedx, openedx, and _videos:yes (if present) added automatically",
    )

    parser.add_argument(
        "--convert-in-webm",
        help="Re-encode videos to WebM",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--ignore-missing-xblocks",
        help="Ignore unsupported content (xblock)",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--lang",
        help="Default language of the interface and the ZIM content (ISO-639-1 codes)",
    )

    parser.add_argument(
        "--add-wiki",
        help="Add wiki (if available) to the ZIM",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--add-forum",
        help="Add forum (if available) to the ZIM",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--output",
        help="Output folder for ZIM file",
        default="output",
        dest="output_dir",
    )

    parser.add_argument(
        "--tmp-dir",
        help="Path to create temp folder in. Used for building ZIM file. Receives all data",
    )

    parser.add_argument(
        "--zim-file",
        help="ZIM file name (based on --name if not provided)",
        dest="fname",
    )

    parser.add_argument(
        "--no-fulltext-index",
        help="Don't index the scraped content in the ZIM",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--no-zim",
        help="Don't produce a ZIM file, create build folder only.",
        action="store_true",
        default=False,
    )

    parser.add_argument(
        "--keep",
        help="Don't remove build folder on start (for debug/devel)",
        default=False,
        action="store_true",
        dest="keep_build_dir",
    )

    parser.add_argument(
        "--debug", help="Enable verbose output", action="store_true", default=False
    )

    parser.add_argument(
        "--version",
        help="Display scraper version and exit",
        action="version",
        version=SCRAPER,
    )

    args = parser.parse_args()
    setDebug(args.debug)
    logger = getLogger()

    from .scraper import Openedx2Zim

    try:
        scraper = Openedx2Zim(**dict(args._get_kwargs()))
        scraper.run()
    except Exception as exc:
        logger.error(f"FAILED. An error occurred: {exc}")
        if args.debug:
            logger.exception(exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()