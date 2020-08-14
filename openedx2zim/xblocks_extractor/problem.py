import json
import uuid
import itertools
import urllib

from bs4 import BeautifulSoup

from .base_xblock import BaseXblock
from ..utils import jinja, get_back_jumps
from ..constants import getLogger


logger = getLogger()


class Problem(BaseXblock):
    def __init__(
        self, xblock_json, relative_path, root_url, xblock_id, descendants, scraper
    ):
        super().__init__(
            xblock_json, relative_path, root_url, xblock_id, descendants, scraper
        )

        # extra vars
        self.is_video = False
        self.problem_header = ""
        self.html_content = ""
        self.answers = {}
        self.explanation = []
        self.problem_id = None
        self.answers_available = False
        self.xmodule_handler = None

    def check_problem_type_and_get_options(self, problem_tag):
        """ returns whether answers are fetchable for a problem, if yes, also returns whether the problem
            has single correct answers, and a list of options for answers """

        single_answer_correct_options = problem_tag.find_all(
            "input", attrs={"type": "radio"}
        )
        if len(single_answer_correct_options) > 1:
            return True, True, single_answer_correct_options
        multi_answer_correct_options = problem_tag.find_all(
            "input", attrs={"type": "checkbox"}
        )
        if (
            len(multi_answer_correct_options) > 1
            and len(multi_answer_correct_options) <= 5
        ):
            return True, False, multi_answer_correct_options
        return False, None, None

    def check_answer(self, answer_candidate, instance_connection):
        """ call the instance API with a answer candidate (a set of options) and return the response """

        post_data = {}

        # prepare the payload to be sent
        for candidate in answer_candidate:
            post_data.update(
                {
                    candidate.attrs.get("name"): candidate.attrs.get("id")[
                        len(candidate.attrs.get("name")) + 1 :
                    ]
                }
            )

        # encode the payload as a byte string
        post_data = urllib.parse.urlencode(post_data).encode("utf-8")

        # send a POST request to the instance with the payload
        return instance_connection.get_api_json(
            f"{self.xmodule_handler}/problem_check",
            post_data=post_data,
            referer=self.xblock_json["student_view_url"],
        )

    def write_answers_and_mark_available(self):
        """ saves the answers and marks them available """

        # write the answer content in a javascript file
        with open(
            self.scraper.instance_assets_dir.joinpath(
                f"{self.problem_id}_answers.js"
            ),
            "w",
        ) as answer_file:
            answer_file.write(
                f"problem_answers['{self.problem_id}']="
                + json.dumps(self.answers, indent=4)
            )

        # mark answers available
        self.answers_available = True

    def get_answers(self, instance_connection, problem_tag):
        """ retrieve answers to a problem if the answers are retrievable

            answers can only be retrieved if the problem is a multiple choice question with
            single correct answers or with multiple correct answers (only if number of options <= 5)
            the answers are saved in a js file in instance_assets directory """

        def get_html_replacement_content(result):
            """ get the content with which the content of the problem be replaced from the response from API """

            spup = BeautifulSoup(result["contents"], "lxml")
            return (
                spup.find("div", attrs={"class": "problem"})
                .find("div")
                .find("div")
            )

        # check answer fetchability, problem type, and get options if applicable
        (
            answers_fetchable,
            single_correct,
            options_list,
        ) = self.check_problem_type_and_get_options(problem_tag)

        if answers_fetchable:
            # answer fetching is feasible
            if single_correct:
                # fetch answer for single correct question
                for answer_candidate in options_list:
                    result = self.check_answer([answer_candidate], instance_connection)
                    if result["success"] in ["correct", "incorrect"]:
                        html_content_to_replace = get_html_replacement_content(result)
                        self.answers.update(
                            {
                                answer_candidate.attrs.get("id"): str(
                                    html_content_to_replace
                                )
                            }
                        )
                    else:
                        logger.error("Answer fetching failed...")
                        return
            else:
                # fetch answer for multiple correct question
                # generate all possible combinations
                for r in range(1, len(options_list) + 1):
                    for answer_candidate in itertools.combinations(options_list, r):
                        result = self.check_answer(answer_candidate, instance_connection)
                        if result["success"] in ["correct", "incorrect"]:
                            html_content_to_replace = get_html_replacement_content(result)
                            self.answers.update(
                                {
                                    "-".join(
                                        answer.attrs.get("id")
                                        for answer in answer_candidate
                                    ): str(html_content_to_replace)
                                }
                            )
                        else:
                            logger.error("Answer fetching failed...")
                            return

            self.write_answers_and_mark_available()

        else:
            logger.warning("Answer fetching for this type of problem is not supported")

    def clean_problem_content(self, soup):
        """ removes unnecessary content from the problem content """

        # remove all notifications
        for div in soup.find_all("div", attrs={"class": "notification"}):
            div.decompose()

        # clear all inputs
        for input_tag in soup.find_all("input"):
            if input_tag.has_attr("value"):
                input_tag["value"] = ""
            if input_tag.has_attr("checked"):
                del input_tag.attrs["checked"]

        # clear all previously answered labels
        label_list = soup.find_all("label", attrs={"class": "response-label"})
        for label in label_list:
            if "choicegroup_correct" in label["class"]:
                label["class"].remove("choicegroup_correct")

        # clear messages (if previously answered on instance)
        span_message = soup.find("span", attrs={"class": "message"})
        if span_message:
            span_message.decompose()

        # remove action bar (contains the submission button)
        soup.find("div", attrs={"class": "action"}).decompose()
        for span in soup.find_all("span", attrs={"class": "unanswered"}):
            span.decompose()
        for span in soup.find_all("span", attrs={"class": "sr"}):
            span.decompose()

    def download(self, instance_connection):
        """ download the problem xblock content from the instance """

        # try to fetch content
        content = instance_connection.get_page(self.xblock_json["student_view_url"])
        if not content:
            return
        raw_soup = BeautifulSoup(content, "lxml")
        self.xmodule_handler = str(
            raw_soup.find("div", attrs={"class": "problems-wrapper"})["data-url"]
        )
        try:
            html_content_from_div = str(
                raw_soup.find("div", attrs={"class": "problems-wrapper"})[
                    "data-content"
                ]
            )
        except Exception:
            html_content_from_div = str(
                instance_connection.get_api_json(self.xmodule_handler + "/problem_get")[
                    "html"
                ]
            )

        # assign a random problem ID
        self.problem_id = str(uuid.uuid4())

        # create the soup
        soup = BeautifulSoup(html_content_from_div, "lxml")

        # clean the soup content
        self.clean_problem_content(soup)

        # get the problem header
        self.problem_header = str(soup.find("h3", attrs={"class": "problem-header"}))

        # get the main problem content
        problem_tag = soup.find("div", attrs={"class": "problem"})

        # try to get answers
        self.get_answers(instance_connection, problem_tag)

        # process final HTML content
        html_content = self.scraper.html_processor.dl_dependencies_and_fix_links(
            content=str(problem_tag.find("div")),
            output_path=self.scraper.instance_assets_dir,
            path_from_html=get_back_jumps(5) + "instance_assets",
            root_from_html=get_back_jumps(5),
        )

        # defer scripts in the HTML as they sometimes are inline and tend
        # to access content below them
        html_content = self.scraper.html_processor.defer_scripts(
            content=html_content,
            output_path=self.output_path,
            path_from_html=self.folder_name,
        )

        # save the content
        self.html_content = str(html_content)

    def render(self):
        """ render the fetched content and return it """

        return jinja(
            None,
            "problem.html",
            False,
            problem_id=self.problem_id,
            problem_header=self.problem_header,
            html_content=self.html_content,
            path_to_root="../" * 5,
            answers_available=self.answers_available,
        )
