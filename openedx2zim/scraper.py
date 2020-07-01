#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import sys
import os
import re
import tempfile
import pathlib
import uuid
import distutils
import urllib

import bs4 as BeautifulSoup
from slugify import slugify
from zimscraperlib.zim import ZimInfo, make_zim_file

from .utils import (
    check_missing_binary,
    jinja_init,
    create_zims,
    make_dir,
    download,
    dl_dependencies,
    jinja,
)
from .connection import Connection
from .constants import SCRAPER, BLOCKS_TYPE, getLogger
from .annexe import wiki, forum, booknav, render_wiki, render_forum, render_booknav


logger = getLogger()

class 


class Openedx2Zim:
    def __init__(
        self,
        course_url,
        email,
        password,
        name,
        title,
        description,
        creator,
        publisher,
        tags,
        convert_in_webm,
        ignore_missing_xblock,
        lang,
        add_wiki,
        add_forum,
        output_dir,
        tmp_dir,
        fname,
        no_fulltext_index,
        no_zim,
        keep_build_dir,
        debug,
    ):

        # video-encoding info
        self.convert_in_webm = convert_in_webm

        # zim params
        self.fname = fname
        self.tags = [] if tags is None else [t.strip() for t in tags.split(",")]
        self.title = title
        self.description = description
        self.creator = creator
        self.publisher = publisher
        self.name = name
        self.lang = lang or "en"
        self.no_fulltext_index = no_fulltext_index

        # directory setup
        self.output_dir = pathlib.Path(output_dir).expanduser().resolve()
        if tmp_dir:
            pathlib.Path(tmp_dir).mkdir(parents=True, exist_ok=True)
        self.build_dir = pathlib.Path(tempfile.mkdtemp(dir=tmp_dir))

        # zim info
        self.zim_info = ZimInfo(
            # index path shall be updated
            homepage="index.html",
            tags=self.tags + ["_category:openedx", "openedx"], # put videos-yes
            creator=self.creator,
            publisher=self.publisher,
            name=self.name,
            scraper=SCRAPER,
            favicon="favicon.png",
        )

        # scraper options
        self.course_url = course_url
        self.add_wiki = add_wiki
        self.add_forum = add_forum
        self.ignore_missing_xblock = ignore_missing_xblock

        # authentication
        self.email = email
        self.password = password

        # debug/developer options
        self.no_zim = no_zim
        self.debug = debug

        self.name = None
        self.info = None
        self.json = None
        self.instance_url = None
        self.course_id = None
        self.block_id_id = None
        self.json_tree = None
        self.root_id = None
        self.course_root = None
        self.path = ""
        self.rooturl = ""
        self.top = {}
        self.object = []
        self.no_homepage = False
        self.forum_thread = None
        self.page_annexe = []
        self.book_list_list = []

    def get_course_id(self, url, course_page_name, course_prefix, instance_url):
        clean_url = re.match(
            instance_url + course_prefix + ".*" + course_page_name, url
        )
        clean_id = clean_url.group(0)[
            len(instance_url + course_prefix) : -len(course_page_name)
        ]
        if "%3" in clean_id:  # course_id seems already encode
            return clean_id
        else:
            return urllib.parse.quote_plus(clean_id)

    def prepare(self, connection):
        self.instance_url = connection.conf["instance_url"]
        self.course_id = self.get_course_id(
            self.course_url,
            connection.conf["course_page_name"],
            connection.conf["course_prefix"],
            self.instance_url,
        )
        logger.info("Getting course info ...")
        self.info = connection.get_api_json(
            "/api/courses/v1/courses/" + self.course_id + "?username=" + connection.user
        )
        self.name = slugify(self.info["name"])
        logger.info("Getting course xblocks ...")
        json_response = connection.get_api_json(
            "/api/courses/v1/blocks/?course_id="
            + self.course_id
            + "&username="
            + connection.user
            + "&depth=all&requested_fields=graded,format,student_view_multi_device&student_view_data=video,discussion&block_counts=video,discussion,problem&nav_depth=3"
        )
        self.json = json_response["blocks"]
        self.root_id = json_response["root"]

    def parse_json(self):
        def make_objects(current_path, current_id, rooturl):
            current_json = self.json[current_id]
            path = os.path.join(current_path, slugify(current_json["display_name"]))
            rooturl = rooturl + "../"
            random_id = str(uuid.uuid4())
            descendants = None
            if "descendants" in current_json:
                descendants = []
                for next_id in current_json["descendants"]:
                    descendants.append(make_objects(path, next_id, rooturl))
            if current_json["type"] in BLOCKS_TYPE:
                obj = BLOCKS_TYPE[current_json["type"]](
                    current_json, path, rooturl, random_id, descendants, self
                )
            else:
                if not self.ignore_missing_xblock:
                    logger.error(
                        "Some part of your course are not supported by openedx2zim : {} ({})\n You should open an issue at https://github.com/openzim/openedx/issues (with this message and Mooc URL, you can ignore this with --ignore-unsupported-xblocks".format(
                            current_json["type"], current_json["student_view_url"]
                        )
                    )
                    sys.exit(1)
                else:
                    logger.warning(
                        "Unavailable xblocks: " + current_json["student_view_url"]
                    )
                    obj = BLOCKS_TYPE["unavailable"](
                        current_json, path, rooturl, random_id, descendants, self
                    )

            if current_json["type"] == "course":
                self.head = obj
            self.object.append(obj)
            return obj

        logger.info("Parse json and make folder tree")
        make_objects(self.path + "course/", self.root_id, self.rooturl + "../")

    def annexe(self, connection):
        logger.info("Try to get specific page of mooc")
        content = connection.get_page(self.course_url)
        soup = BeautifulSoup.BeautifulSoup(content, "lxml")
        top_bs = (
            soup.find("ol", attrs={"class": "course-material"})
            or soup.find("ul", attrs={"class": "course-material"})
            or soup.find("ul", attrs={"class": "navbar-nav"})
            or soup.find("ol", attrs={"class": "course-tabs"})
        )
        if top_bs is not None:
            for top_elem in top_bs.find_all("li"):
                top_elem = top_elem.find("a")
                if top_elem["href"][-1] == "/":
                    path = top_elem["href"][:-1].split("/")[-1]
                else:
                    path = top_elem["href"].split("/")[-1]
                if path == "course" or "courseware" in path:
                    name = top_elem.get_text().replace(", current location", "")
                    self.top[name] = "course/" + self.head.folder_name + "/index.html"
                if "info" in path:
                    name = top_elem.get_text().replace(", current location", "")
                    self.top[name] = "/index.html"
                if (
                    path == "course"
                    or "edxnotes" in path
                    or "progress" in path
                    or "info" in path
                    or "courseware" in path
                ):
                    continue
                if "wiki" in path and self.add_wiki:
                    self.wiki, self.wiki_name, path = wiki(connection, self)
                elif "forum" in path and self.add_forum:
                    path = "forum/"
                    (
                        self.forum_thread,
                        self.forum_category,
                        self.staff_user_forum,
                    ) = forum(connection, self)
                elif ("wiki" not in path) and ("forum" not in path):
                    output_path = os.path.join(self.build_dir, path)
                    make_dir(output_path)
                    page_content = connection.get_page(self.instance_url + top_elem["href"])
                    soup_page = BeautifulSoup.BeautifulSoup(page_content, "lxml")
                    just_content = soup_page.find(
                        "section", attrs={"class": "container"}
                    )
                    if just_content is not None:
                        html_content = dl_dependencies(
                            str(just_content), output_path, "", connection
                        )
                        self.page_annexe.append(
                            {
                                "output_path": output_path,
                                "content": html_content,
                                "title": soup_page.find("title").get_text(),
                            }
                        )
                    else:
                        book = soup_page.find(
                            "section", attrs={"class": "book-sidebar"}
                        )
                        if book is not None:
                            self.book_list_list.append(
                                {
                                    "output_path": output_path,
                                    "book_list": booknav(self, book, output_path),
                                    "dir_path": path,
                                }
                            )
                        else:
                            logger.warning(
                                "Oh it's seems we does not support one type of extra content (in top bar) :"
                                + path
                            )
                            continue
                self.top[top_elem.get_text()] = path + "/index.html"

    def download(self, connection):
        download(
            "https://www.google.com/s2/favicons?domain=" + self.instance_url,
            os.path.join(self.build_dir, "favicon.png"),
            None,
        )

        logger.info("Get homepage")
        content = connection.get_page(self.course_url)
        make_dir(os.path.join(self.build_dir, "home"))
        self.html_homepage = []
        soup = BeautifulSoup.BeautifulSoup(content, "lxml")
        html_content = soup.find("div", attrs={"class": "welcome-message"})
        if html_content is None:
            html_content = soup.find_all(
                "div", attrs={"class": re.compile("info-wrapper")}
            )
            if html_content == []:
                self.no_homepage = True
            else:
                for x in range(0, len(html_content)):
                    article = html_content[x]
                    dismiss = article.find("div", attrs={"class": "dismiss-message"})
                    if dismiss is not None:
                        dismiss.decompose()
                    bookmark = article.find(
                        "a", attrs={"class": "action-show-bookmarks"}
                    )
                    if bookmark is not None:
                        bookmark.decompose()
                    buttons = article.find_all(
                        "button", attrs={"class": "toggle-visibility-button"}
                    )
                    if buttons is not None:
                        for button in buttons:
                            button.decompose()
                    article["class"] = "toggle-visibility-element article-content"
                    self.html_homepage.append(
                        dl_dependencies(
                            article.prettify(),
                            os.path.join(self.build_dir, "home"),
                            "home",
                            connection,
                        )
                    )
        else:
            dismiss = html_content.find("div", attrs={"class": "dismiss-message"})
            if dismiss is not None:
                dismiss.decompose()
            bookmark = html_content.find("a", attrs={"class": "action-show-bookmarks"})
            if bookmark is not None:
                bookmark.decompose()
            buttons = html_content.find_all(
                "button", attrs={"class": "toggle-visibility-button"}
            )
            if buttons is not None:
                for button in buttons:
                    button.decompose()
            self.html_homepage.append(
                dl_dependencies(
                    html_content.prettify(),
                    os.path.join(self.build_dir, "home"),
                    "home",
                    connection,
                )
            )
        logger.info("Get content")
        for x in self.object:
            x.download(connection)

    def render(self):
        self.head.render()  # Render course
        for data in self.page_annexe:  # Render annexe
            jinja(
                os.path.join(data["output_path"], "index.html"),
                "specific_page.html",
                False,
                title=data["title"],
                mooc=self,
                content=data["content"],
                rooturl="../../",
            )

        if hasattr(self, "wiki"):
            render_wiki(self)
        if hasattr(self, "forum_category"):
            render_forum(self)
        if len(self.book_list_list) != 0:
            render_booknav(self)
        if not self.no_homepage:
            jinja(  # Render home page
                os.path.join(self.build_dir, "index.html"),
                "home.html",
                False,
                messages=self.html_homepage,
                mooc=self,
                render_homepage=True,
            )
        distutils.dir_util.copy_tree(
            os.path.join(os.path.abspath(os.path.dirname(__file__)), "static"),
            os.path.join(self.build_dir, "static"),
        )

    def zim(self, publisher, zimpath, nofulltextindex, scraper_name):
        logger.info("Create zim")
        if self.no_homepage:
            homepage = os.path.join(self.head.path, "index.html")
        else:
            homepage = "index.html"
        create_zims(
            self.info["name"],
            self.lang,
            publisher,
            self.info["short_description"],
            self.info["org"],
            self.build_dir,
            zimpath,
            nofulltextindex,
            homepage,
            self.course_url,
            scraper_name,
        )

    def run(self):
        logger.info(
            f"Starting {SCRAPER} with:\n"
            f"  Course URL: {self.course_url}\n"
            f"  Email ID: {self.email}"
        )
        logger.debug("Checking for missing binaries")
        check_missing_binary(self.no_zim)
        logger.debug("Testing credentials")
        connection = Connection(self.password, self.course_url, self.email)
        jinja_init()
        self.prepare(connection)
        self.parse_json()
        self.annexe(connection)
        self.download(connection)
        self.render()
        if not self.no_zim:
            self.zim(
                self.publisher, self.zimpath, self.no_fulltext_index, SCRAPER,
            )
