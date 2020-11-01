#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import concurrent.futures
import datetime
import locale
import os
import pathlib
import re
import shutil
import sys
import tempfile
import urllib
import uuid

from bs4 import BeautifulSoup
from kiwixstorage import KiwixStorage
from pif import get_public_ip
from slugify import slugify
from zimscraperlib.download import BestMp4, BestWebm, YoutubeDownloader, save_large_file
from zimscraperlib.i18n import get_language_details, setlocale, _
from zimscraperlib.image.convertion import convert_image
from zimscraperlib.image.transformation import resize_image
from zimscraperlib.video.encoding import reencode
from zimscraperlib.video.presets import (
    VideoMp4Low,
    VideoWebmLow,
    VideoMp4High,
    VideoWebmHigh,
)
from zimscraperlib.zim import make_zim_file

from .annex import MoocForum, MoocWiki
from .constants import (
    IMAGE_FORMATS,
    OPTIMIZER_VERSIONS,
    ROOT_DIR,
    SCRAPER,
    VIDEO_FORMATS,
    getLogger,
)
from .html_processor import HtmlProcessor
from .instance_connection import InstanceConnection
from .utils import (
    check_missing_binary,
    exec_cmd,
    get_back_jumps,
    get_meta_from_url,
    jinja,
    jinja_init,
    prepare_url,
)
from .xblocks_extractor.chapter import Chapter
from .xblocks_extractor.course import Course
from .xblocks_extractor.discussion import Discussion
from .xblocks_extractor.drag_and_drop_v2 import DragAndDropV2
from .xblocks_extractor.free_text_response import FreeTextResponse
from .xblocks_extractor.html import Html
from .xblocks_extractor.libcast import Libcast
from .xblocks_extractor.lti import Lti
from .xblocks_extractor.problem import Problem
from .xblocks_extractor.sequential import Sequential
from .xblocks_extractor.unavailable import Unavailable
from .xblocks_extractor.vertical import Vertical
from .xblocks_extractor.video import Video

XBLOCK_EXTRACTORS = {
    "course": Course,
    "chapter": Chapter,
    "sequential": Sequential,
    "vertical": Vertical,
    "video": Video,
    "libcast_xblock": Libcast,
    "html": Html,
    "problem": Problem,
    "discussion": Discussion,
    "qualtricssurvey": Html,
    "freetextresponse": FreeTextResponse,
    "drag-and-drop-v2": DragAndDropV2,
    "lti": Lti,
    "unavailable": Unavailable,
}

logger = getLogger()


class Openedx2Zim:
    def __init__(
        self,
        course_url,
        email,
        password,
        video_format,
        low_quality,
        autoplay,
        locale_name,
        name,
        title,
        description,
        creator,
        publisher,
        tags,
        ignore_missing_xblocks,
        instance_login_page,
        instance_course_page,
        instance_course_prefix,
        favicon_url,
        add_wiki,
        add_forum,
        remove_seq_nav,
        s3_url_with_credentials,
        use_any_optimized_version,
        output_dir,
        tmp_dir,
        fname,
        no_fulltext_index,
        no_zim,
        keep_build_dir,
        debug,
        threads,
    ):

        # video-encoding info
        self.video_format = video_format
        self.low_quality = low_quality

        # zim params
        self.fname = fname
        self.tags = [] if tags is None else [t.strip() for t in tags.split(",")]
        self.title = title
        self.description = description
        self.creator = creator
        self.publisher = publisher
        self.name = name
        self.no_fulltext_index = no_fulltext_index

        # directory setup
        self.output_dir = pathlib.Path(output_dir).expanduser().resolve()
        if tmp_dir:
            pathlib.Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        self.build_dir = pathlib.Path(tempfile.mkdtemp(dir=tmp_dir))

        # scraper options
        self.course_url = course_url
        self.favicon_url = favicon_url
        self.add_wiki = add_wiki
        self.add_forum = add_forum
        self.ignore_missing_xblocks = ignore_missing_xblocks
        self.autoplay = autoplay
        self.remove_seq_nav = remove_seq_nav
        self.threads = threads
        self.yt_downloader = YoutubeDownloader(threads=1)

        # authentication
        self.email = email
        self.password = password

        # instance config
        self.instance_config = {
            "login_page": instance_login_page,
            "course_page_name": instance_course_page,
            "course_prefix": instance_course_prefix,
        }

        # optimization cache
        self.s3_url_with_credentials = s3_url_with_credentials
        self.use_any_optimized_version = use_any_optimized_version
        self.s3_storage = None

        # debug/developer options
        self.no_zim = no_zim
        self.debug = debug
        self.keep_build_dir = keep_build_dir

        # course info
        self.course_id = None
        self.instance_url = None
        self.course_info = None
        self.course_name_slug = None
        self.has_homepage = True

        # scraper data
        self.instance_connection = None
        self.html_processor = None
        self.xblock_extractor_objects = []
        self.head_course_xblock = None
        self.homepage_html = []
        self.annexed_pages = []
        self.book_lists = []
        self.course_tabs = {}
        self.course_xblocks = None
        self.root_xblock_id = None
        self.wiki = None
        self.forum = None

        self.failed_urls = {}
        self.failure_threshhold = 1

        # set and record locale for translations
        locale_details = get_language_details(locale_name)
        self.instance_lang = locale_details["iso-639-1"]
        if locale_details["querytype"] != "locale":
            locale_name = self.instance_lang
        try:
            self.locale = setlocale(ROOT_DIR, locale_name)
        except locale.Error:
            logger.error(
                f"No locale for {locale_name}. Use --locale to specify it. defaulting to en_US"
            )
            self.locale = setlocale(ROOT_DIR, "en")

    @property
    def instance_assets_dir(self):
        return self.build_dir.joinpath("instance_assets")

    def get_course_id(self, url, course_page_name, course_prefix, instance_url):
        clean_url = re.match(
            instance_url + course_prefix + ".*" + course_page_name, url
        )
        clean_id = clean_url.group(0)[
            len(instance_url + course_prefix) : -len(course_page_name)
        ]
        if "%3" in clean_id:  # course_id seems already encode
            return clean_id
        return urllib.parse.quote_plus(clean_id)

    def prepare_mooc_data(self):
        self.instance_url = self.instance_config["instance_url"]
        self.course_id = self.get_course_id(
            self.course_url,
            self.instance_config["course_page_name"],
            self.instance_config["course_prefix"],
            self.instance_url,
        )
        logger.info("Getting course info ...")
        self.course_info = self.instance_connection.get_api_json(
            "/api/courses/v1/courses/"
            + self.course_id
            + "?username="
            + self.instance_connection.user
        )
        self.course_name_slug = slugify(self.course_info["name"])
        logger.info("Getting course xblocks ...")
        xblocks_data = self.instance_connection.get_api_json(
            "/api/courses/v1/blocks/?course_id="
            + self.course_id
            + "&username="
            + self.instance_connection.user
            + "&depth=all&requested_fields=graded,format,student_view_multi_device&student_view_data=video,discussion&block_counts=video,discussion,problem&nav_depth=3"
        )
        self.course_xblocks = xblocks_data["blocks"]
        self.root_xblock_id = xblocks_data["root"]

    def parse_course_xblocks(self):
        def make_objects(current_path, current_id, root_url):
            current_xblock = self.course_xblocks[current_id]
            # ensure the display name is not empty to avoid path issues
            if not current_xblock["display_name"]:
                current_xblock["display_name"] = "xblock"
            xblock_path = current_path.joinpath(slugify(current_xblock["display_name"]))

            # update root url respective to the current xblock
            root_url = root_url + "../"
            random_id = str(uuid.uuid4())
            descendants = None

            # recursively make objects for all descendents
            if "descendants" in current_xblock:
                descendants = []
                for next_xblock_id in current_xblock["descendants"]:
                    descendants.append(
                        make_objects(xblock_path, next_xblock_id, root_url)
                    )

            # create objects of respective xblock_extractor if available
            if current_xblock["type"] in XBLOCK_EXTRACTORS:
                obj = XBLOCK_EXTRACTORS[current_xblock["type"]](
                    xblock_json=current_xblock,
                    relative_path=xblock_path,
                    root_url=root_url,
                    xblock_id=random_id,
                    descendants=descendants,
                    scraper=self,
                )
            else:
                if not self.ignore_missing_xblocks:
                    logger.error(
                        f"Unsupported xblock: {current_xblock['type']} URL: {current_xblock['student_view_url']}"
                        f"  You can open an issue at https://github.com/openzim/openedx/issues with this log and MOOC URL"
                        f"  You can ignore this message by passing --ignore-missing-xblocks in atguments"
                    )
                    sys.exit(1)
                else:
                    logger.warning(
                        f"Ignoring unsupported xblock: {current_xblock['type']} URL: {current_xblock['student_view_url']}"
                    )
                    # make an object of unavailable type
                    obj = XBLOCK_EXTRACTORS["unavailable"](
                        xblock_json=current_xblock,
                        relative_path=xblock_path,
                        root_url=root_url,
                        xblock_id=random_id,
                        descendants=descendants,
                        scraper=self,
                    )

            if current_xblock["type"] == "course":
                self.head_course_xblock = obj
            self.xblock_extractor_objects.append(obj)
            return obj

        logger.info("Parsing xblocks and preparing extractor objects ...")
        make_objects(
            current_path=pathlib.Path("course"),
            current_id=self.root_xblock_id,
            root_url="../",
        )

    def get_book_list(self, book, output_path):
        pdf = book.find_all("a")
        book_list = []
        for url in pdf:
            file_name = pathlib.Path(urllib.parse.urlparse(url["rel"][0]).path).name
            if self.download_file(
                prepare_url(url["rel"][0], self.instance_url),
                output_path.joinpath(file_name),
            ):
                book_list.append({"url": file_name, "name": url.get_text()})
        return book_list

    def annex_extra_page(self, tab_href, tab_org_path):
        output_path = self.build_dir.joinpath(tab_org_path)
        output_path.mkdir(parents=True, exist_ok=True)
        page_content = self.instance_connection.get_page(self.instance_url + tab_href)
        if not page_content:
            logger.error(f"Failed to get page content for tab {tab_org_path}")
            raise SystemExit(1)
        soup_page = BeautifulSoup(page_content, "lxml")
        just_content = soup_page.find("section", attrs={"class": "container"})

        # its a content page
        if just_content is not None:
            self.annexed_pages.append(
                {
                    "output_path": output_path,
                    "content": soup_page,
                    "title": soup_page.find("title").get_text(),
                }
            )
            return f"{tab_org_path}/index.html"

        # page contains a book_list
        book = soup_page.find("section", attrs={"class": "book-sidebar"})
        if book is not None:
            self.book_lists.append(
                {
                    "output_path": output_path,
                    "book_list": book,
                    "dir_path": tab_org_path,
                }
            )
            return f"{tab_org_path}/index.html"

        # page is not supported
        logger.warning(
            "Oh it's seems we does not support one type of extra content (in top bar) :"
            + tab_org_path
        )
        shutil.rmtree(output_path, ignore_errors=True)
        return None

    def get_tab_path_and_name(self, tab_text, tab_href):
        # set tab_org_path based on tab_href
        if tab_href[-1] == "/":
            tab_org_path = tab_href[:-1].split("/")[-1]
        else:
            tab_org_path = tab_href.split("/")[-1]

        # default value for tab_name and tab_path
        tab_name = tab_text
        tab_path = None

        # check for paths in org_tab_path
        if tab_org_path == "course" or "courseware" in tab_org_path:
            tab_name = _("Course")
            tab_path = "course/" + self.head_course_xblock.folder_name + "/index.html"
        elif "info" in tab_org_path:
            tab_name = _("Course Info")
            tab_path = "/index.html"
        elif "wiki" in tab_org_path and self.add_wiki:
            self.wiki = MoocWiki(self)
            tab_path = f"{str(self.wiki.wiki_path)}/index.html"
        elif "forum" in tab_org_path and self.add_forum:
            self.forum = MoocForum(self)
            tab_path = "forum/index.html"
        elif ("wiki" not in tab_org_path) and ("forum" not in tab_org_path):
            # check if already in dict
            for key in self.course_tabs:
                if self.course_tabs[key] == f"{tab_org_path}/index.html":
                    tab_path = self.course_tabs[key]
                    break
            else:
                tab_path = self.annex_extra_page(tab_href, tab_org_path)
        return tab_name, tab_path

    def get_course_tabs(self):
        logger.info("Getting course tabs ...")
        content = self.instance_connection.get_page(self.course_url)
        if not content:
            logger.error("Failed to get course tabs")
            raise SystemExit(1)
        soup = BeautifulSoup(content, "lxml")
        course_tabs = (
            soup.find("ol", attrs={"class": "course-material"})
            or soup.find("ul", attrs={"class": "course-material"})
            or soup.find("ul", attrs={"class": "navbar-nav"})
            or soup.find("ol", attrs={"class": "course-tabs"})
        )
        if course_tabs is not None:
            for tab in course_tabs.find_all("li"):
                tab_name, tab_path = self.get_tab_path_and_name(
                    tab_text=tab.get_text(), tab_href=tab.find("a")["href"]
                )
                if tab_name is not None and tab_path is not None:
                    self.course_tabs[tab_name] = tab_path

    def annex(self):
        self.get_course_tabs()
        logger.info("Downloading content for extra pages ...")
        for page in self.annexed_pages:
            root_from_html = get_back_jumps(
                len(page["output_path"].relative_to(self.build_dir).parts)
            )
            soup = page["content"]
            path_from_html = root_from_html + "instance_assets"
            extra_head_content = self.html_processor.extract_head_css_js(
                soup=soup,
                output_path=self.instance_assets_dir,
                path_from_html=path_from_html,
                root_from_html=root_from_html,
            )
            body_end_scripts = self.html_processor.extract_body_end_scripts(
                soup=soup,
                output_path=self.instance_assets_dir,
                path_from_html=path_from_html,
                root_from_html=root_from_html,
            )
            page["content"] = self.html_processor.dl_dependencies_and_fix_links(
                content=str(soup.find("div", attrs={"class": "xblock"})),
                output_path=self.instance_assets_dir,
                path_from_html=path_from_html,
                root_from_html=root_from_html,
            )
            page.update(
                {
                    "extra_head_content": extra_head_content,
                    "body_end_scripts": body_end_scripts,
                }
            )

        logger.info("Processing book lists ...")
        for item in self.book_lists:
            item["book_list"] = self.get_book_list(
                item["book_list"], item["output_path"]
            )

        # annex wiki if available
        if self.wiki:
            logger.info("Annexing wiki ...")
            self.wiki.annex_wiki()

        # annex forum if available
        if self.forum:
            logger.info("Annexing forum ...")
            self.forum.annex_forum()

    def get_favicon(self):
        """ get the favicon from the given URL for the instance or the fallback URL """

        favicon_fpath = self.build_dir.joinpath("favicon.png")

        # download the favicon
        save_large_file(self.favicon_url, favicon_fpath)

        # convert and resize
        convert_image(favicon_fpath, favicon_fpath, fmt="PNG")
        resize_image(favicon_fpath, 48, allow_upscaling=True)

        if not favicon_fpath.exists():
            raise Exception("Favicon download failed")

    def get_content(self):
        """ download the content for the course """

        def clean_content(html_article):
            """ removes unwanted elements from homepage html """

            unwanted_elements = {
                "div": {"class": "dismiss-message"},
                "a": {"class": "action-show-bookmarks"},
                "button": {"class": "toggle-visibility-button"},
            }
            for element_type, attribute in unwanted_elements.items():
                element = html_article.find(element_type, attrs=attribute)
                if element:
                    element.decompose()

        # download favicon
        self.get_favicon()

        # get the course url and generate homepage
        logger.info("Getting homepage ...")
        content = self.instance_connection.get_page(self.course_url)
        if not content:
            logger.error("Error while getting homepage")
            raise SystemExit(1)
        self.build_dir.joinpath("home").mkdir(parents=True, exist_ok=True)
        soup = BeautifulSoup(content, "lxml")

        # save the direction (ltr or rtl)
        self.is_rtl = True if soup.find("head").attrs["dir"] == "rtl" else False
        welcome_message = soup.find("div", attrs={"class": "welcome-message"})

        # there are multiple welcome messages
        if not welcome_message:
            info_articles = soup.find_all(
                "div", attrs={"class": re.compile("info-wrapper")}
            )
            if info_articles == []:
                self.has_homepage = False
            else:
                for article in info_articles:
                    clean_content(article)
                    article["class"] = "toggle-visibility-element article-content"
                    self.homepage_html.append(
                        self.html_processor.dl_dependencies_and_fix_links(
                            content=article.prettify(),
                            output_path=self.instance_assets_dir,
                            path_from_html="instance_assets",
                            root_from_html="",
                        )
                    )

        # there is a single welcome message
        else:
            clean_content(welcome_message)
            self.homepage_html.append(
                self.html_processor.dl_dependencies_and_fix_links(
                    content=welcome_message.prettify(),
                    output_path=self.instance_assets_dir,
                    path_from_html="instance_assets",
                    root_from_html="",
                )
            )

        # make xblock_extractor objects download their content
        logger.info("Getting content for supported xblocks ...")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.threads
        ) as executor:
            fs = [
                executor.submit(xblock.download, self.instance_connection)
                for xblock in self.xblock_extractor_objects
            ]
            concurrent.futures.wait(fs, return_when=concurrent.futures.ALL_COMPLETED)

    def s3_credentials_ok(self):
        logger.info("Testing S3 Optimization Cache credentials ...")
        self.s3_storage = KiwixStorage(self.s3_url_with_credentials)
        if not self.s3_storage.check_credentials(
            list_buckets=True, bucket=True, write=True, read=True, failsafe=True
        ):
            logger.error("S3 cache instance_connection error testing permissions.")
            logger.error(f"  Server: {self.s3_storage.url.netloc}")
            logger.error(f"  Bucket: {self.s3_storage.bucket_name}")
            logger.error(f"  Key ID: {self.s3_storage.params.get('keyid')}")
            logger.error(f"  Public IP: {get_public_ip()}")
            return False
        return True

    def download_from_cache(self, key, fpath, meta):
        """ whether it downloaded from S3 cache """

        filetype = "jpeg" if fpath.suffix in [".jpeg", ".jpg"] else fpath.suffix[1:]
        if not self.s3_storage.has_object(key) or not meta:
            return False
        meta_dict = {
            "version": meta,
            "optimizer_version": None
            if self.use_any_optimized_version
            else OPTIMIZER_VERSIONS[filetype],
        }
        if not self.s3_storage.has_object_matching(key, meta_dict):
            return False
        try:
            self.s3_storage.download_file(key, fpath)
        except Exception as exc:
            logger.error(f"{key} failed to download from cache: {exc}")
            return False
        logger.info(f"downloaded {fpath} from cache at {key}")
        return True

    def upload_to_cache(self, key, fpath, meta):
        """ whether it uploaded to S3 cache """

        filetype = "jpeg" if fpath.suffix in [".jpeg", ".jpg"] else fpath.suffix[1:]
        if not meta or not filetype:
            return False
        meta = {"version": meta, "optimizer_version": OPTIMIZER_VERSIONS[filetype]}
        try:
            self.s3_storage.upload_file(fpath, key, meta=meta)
        except Exception as exc:
            logger.error(f"{key} failed to upload to cache: {exc}")
            return False
        logger.info(f"uploaded {fpath} to cache at {key}")
        return True

    def downlaod_form_url(self, url, fpath, filetype):
        download_path = fpath
        if (
            filetype
            and (fpath.suffix[1:] != filetype)
            and not (filetype == "jpg" and fpath.suffix[1:] == "jpeg")
        ):
            download_path = pathlib.Path(
                tempfile.NamedTemporaryFile(
                    suffix=f".{filetype}", dir=fpath.parent, delete=False
                ).name
            )
        try:
            save_large_file(url, download_path)
            return download_path
        except Exception as exc:
            logger.error(f"Error while running save_large_file(): {exc}")
            if download_path.exists() and download_path.is_file():
                os.unlink(download_path)
            return None

    def download_from_youtube(self, url, fpath):
        output_file_name = fpath.name.replace(fpath.suffix, "")
        options = (BestWebm if self.video_format == "webm" else BestMp4).get_options(
            target_dir=fpath.parent,
            filepath=pathlib.Path(f"{output_file_name}.%(ext)s"),
            format=f"best[ext={self.video_format}]/best",
            writethumbnail=False,
            write_all_thumbnails=False,
        )
        try:
            self.yt_downloader.download(url, options)
            for content in fpath.parent.iterdir():
                if content.is_file() and content.name.startswith(
                    f"{output_file_name}."
                ):
                    return content
        except Exception as exc:
            logger.error(f"Error while downloading from youtube: {exc}")
            return None

    def convert_video(self, src, dst):
        preset = None
        if self.low_quality:
            preset = VideoWebmLow() if self.video_format == "webm" else VideoMp4Low()
        elif src.suffix[1:] != self.video_format:
            preset = VideoWebmHigh() if self.video_format == "webm" else VideoMp4High()
        else:
            if not src.resolve() == dst.resolve():
                shutil.move(src, dst)
            return True
        return reencode(
            src,
            dst,
            preset.to_ffmpeg_args(),
            delete_src=True,
            failsafe=False,
        )

    def optimize_image(self, src, dst):
        optimized = False
        if src.suffix in [".jpeg", ".jpg"]:
            optimized = (
                exec_cmd("jpegoptim --strip-all -m50 " + str(src), timeout=10) == 0
            )
        elif src.suffix == ".png":
            exec_cmd(
                "pngquant --verbose --nofs --force --ext=.png " + str(src), timeout=10
            )
            exec_cmd("advdef -q -z -4 -i 5  " + str(src), timeout=50)
            optimized = True
        elif src.suffix == ".gif":
            optimized = exec_cmd("gifsicle --batch -O3 -i " + str(src), timeout=10) == 0
        if src.resolve() != dst.resolve():
            shutil.move(src, dst)
        return optimized

    def optimize_file(self, src, dst):
        if src.suffix[1:] in VIDEO_FORMATS:
            return self.convert_video(src, dst)
        if src.suffix[1:] in IMAGE_FORMATS:
            return self.optimize_image(src, dst)

    def generate_s3_key(self, url, fpath):
        if fpath.suffix[1:] in VIDEO_FORMATS:
            quality = "low" if self.low_quality else "high"
        else:
            quality = "default"
        src_url = urllib.parse.urlparse(url)
        prefix = f"{src_url.scheme}://{src_url.netloc}/"
        safe_url = f"{src_url.netloc}/{urllib.parse.quote_plus(src_url.geturl()[len(prefix):])}"
        # safe url looks similar to ww2.someplace.state.gov/data%2F%C3%A9t%C3%A9%2Fsome+chars%2Fimage.jpeg%3Fv%3D122%26from%3Dxxx%23yes
        return f"{fpath.suffix[1:]}/{safe_url}/{quality}"

    def download_file(self, url, fpath):
        """downloads a file from the supplied url to the supplied fpath
        returns true if successful, false if unsuccessful"""

        is_youtube = "youtube" in url
        downloaded_from_cache = False
        meta, filetype = get_meta_from_url(url)
        if self.s3_storage:
            s3_key = self.generate_s3_key(url, fpath)
            downloaded_from_cache = self.download_from_cache(s3_key, fpath, meta)
        if downloaded_from_cache:
            # optimized file downloaded from cache
            return True
        else:
            # file not downloaded from cache
            if is_youtube:
                downloaded_file = self.download_from_youtube(url, fpath)
            else:
                downloaded_file = self.downlaod_form_url(url, fpath, filetype)
            if not downloaded_file:
                logger.error(f"Error while downloading file from URL {url}")
                return False
            try:
                optimized = self.optimize_file(downloaded_file, fpath)
                if self.s3_storage and optimized:
                    self.upload_to_cache(s3_key, fpath, meta)
            except Exception as exc:
                logger.error(f"Error while optimizing {fpath}: {exc}")
                # clean leftovers if any
                if downloaded_file.exists():
                    downloaded_file.unlink()
                if fpath.exists():
                    fpath.unlink()
                return False
            finally:
                if downloaded_file.resolve() != fpath.resolve() and not fpath.exists():
                    shutil.move(downloaded_file, fpath)
                return True

    def render_booknav(self):
        for book_nav in self.book_lists:
            jinja(
                book_nav["output_path"].joinpath("index.html"),
                "booknav.html",
                False,
                book_list=book_nav["book_list"],
                dir_path=book_nav["dir_path"],
                mooc=self,
                rooturl=get_back_jumps(3),
                rtl=self.is_rtl,
            )

    def render(self):
        # Render course
        self.head_course_xblock.render()

        # Render annexed pages
        for page in self.annexed_pages:
            jinja(
                page["output_path"].joinpath("index.html"),
                "specific_page.html",
                False,
                title=page["title"],
                mooc=self,
                content=page["content"],
                extra_headers=page["extra_head_content"],
                body_scripts=page["body_end_scripts"],
                rooturl="../",
                rtl=self.is_rtl,
            )

        # render wiki if available
        if self.wiki:
            self.wiki.render_wiki()

        # render forum if available
        if self.forum:
            self.forum.render_forum()

        # render book lists
        if len(self.book_lists) != 0:
            self.render_booknav()
        if self.has_homepage:
            # render homepage
            jinja(
                self.build_dir.joinpath("index.html"),
                "home.html",
                False,
                messages=self.homepage_html,
                mooc=self,
                render_homepage=True,
                rtl=self.is_rtl,
            )
        shutil.copytree(
            ROOT_DIR.joinpath("templates").joinpath("assets"),
            self.build_dir.joinpath("assets"),
        )

    def get_zim_info(self):
        if not self.has_homepage:
            homepage = f"{self.head_course_xblock.relative_path}/index.html"
        else:
            homepage = "index.html"

        fallback_description = (
            self.course_info["short_description"]
            if self.course_info["short_description"]
            else f"{self.course_info['name']} from {self.course_info['org']}"
        )

        return {
            "description": self.description
            if self.description
            else fallback_description,
            "title": self.title if self.title else self.course_info["name"],
            "creator": self.creator if self.creator else self.course_info["org"],
            "homepage": homepage,
        }

    def run(self):
        logger.info(
            f"Starting {SCRAPER} with:\n"
            f"  Course URL: {self.course_url}\n"
            f"  Email ID: {self.email}"
        )
        logger.debug("Checking for missing binaries")
        check_missing_binary()
        if self.s3_url_with_credentials and not self.s3_credentials_ok():
            raise ValueError("Unable to connect to Optimization Cache. Check its URL.")
        if self.s3_storage:
            logger.info(
                f"Using cache: {self.s3_storage.url.netloc} with bucket: {self.s3_storage.bucket_name}"
            )

        # update instance config
        instance_netloc = urllib.parse.urlparse(self.course_url).netloc
        self.instance_config.update({"instance_url": f"https://{instance_netloc}"})
        logger.info("Testing openedx instance credentials ...")
        self.instance_connection = InstanceConnection(
            self.email,
            self.password,
            self.instance_config,
            self.instance_lang,
        )
        self.instance_connection.establish_connection()
        jinja_init()
        self.instance_assets_dir.mkdir(exist_ok=True, parents=True)
        self.html_processor = HtmlProcessor(self)
        self.prepare_mooc_data()
        self.parse_course_xblocks()
        self.annex()
        self.get_content()
        self.render()
        if not self.no_zim:
            self.fname = (
                self.fname or f"{self.name.replace(' ', '-')}_{{period}}.zim"
            ).format(period=datetime.datetime.now().strftime("%Y-%m"))
            logger.info("building ZIM file")
            zim_info = self.get_zim_info()
            if not self.output_dir.exists():
                self.output_dir.mkdir(parents=True)
            make_zim_file(
                build_dir=self.build_dir,
                fpath=self.output_dir.joinpath(self.fname),
                name=self.name,
                main_page=zim_info["homepage"],
                favicon="favicon.png",
                title=zim_info["title"],
                description=zim_info["description"],
                language="eng",
                creator=zim_info["creator"],
                publisher=self.publisher,
                tags=self.tags + ["_category:other", "openedx"],
                scraper=SCRAPER,
                without_fulltext_index=True if self.no_fulltext_index else False,
            )
            if not self.keep_build_dir:
                logger.info("Removing temp folder...")
                shutil.rmtree(self.build_dir, ignore_errors=True)
        # shutdown the youtube downloader
        self.yt_downloader.shutdown()
        if self.failed_urls:
            logger.error(
                f"Content from {len(self.failed_urls)} URL(s) failed to download"
            )
            logger.debug("Failed URLs:")
            for failed_url in self.failed_urls:
                logger.debug(failed_url)
        logger.info("Done everything")
