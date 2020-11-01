from bs4 import BeautifulSoup

from .base_xblock import BaseXblock
from ..utils import jinja, download_and_convert_subtitles, prepare_url, get_back_jumps
from ..constants import getLogger


logger = getLogger()


class Libcast(BaseXblock):
    def __init__(
        self, xblock_json, relative_path, root_url, xblock_id, descendants, scraper
    ):
        super().__init__(
            xblock_json, relative_path, root_url, xblock_id, descendants, scraper
        )

        # extra vars
        self.subs = []

    def download(self, instance_connection):
        content = instance_connection.get_page(self.xblock_json["student_view_url"])
        if not content:
            return
        soup = BeautifulSoup(content, "lxml")
        url = str(soup.find("video").find("source")["src"])
        subs = soup.find("video").find_all("track")
        if len(subs) != 0:
            subs_lang = {}
            for track in subs:
                if track["src"][0:4] == "http":
                    subs_lang[track["srclang"]] = track["src"]
                else:
                    subs_lang[track["srclang"]] = (
                        self.scraper.instance_url + track["src"]
                    )
            download_and_convert_subtitles(
                self.output_path, subs_lang, instance_connection
            )
            self.subs = [
                {"file": f"{self.folder_name}/{lang}.vtt", "code": lang}
                for lang in subs_lang
            ]

        if self.scraper.video_format == "webm":
            video_path = self.output_path.joinpath("video.webm")
        else:
            video_path = self.output_path.joinpath("video.mp4")
        if not video_path.exists():
            downloaded = self.scraper.download_file(
                prepare_url(url, self.scraper.instance_url), video_path
            )
            if not downloaded:
                logger.error(
                    f"Video for libcast block {self.xblock_json['student_view_url']} failed to download"
                )
                raise SystemExit(1)

    def render(self):
        return jinja(
            None,
            "video.html",
            False,
            format=self.scraper.video_format,
            video_path=f"{self.folder_name}/video.{self.scraper.video_format}",
            title=self.xblock_json["display_name"],
            subs=self.subs,
            autoplay=self.scraper.autoplay,
            path_to_root=get_back_jumps(5),
        )
