from __future__ import absolute_import

import json
import re
import typing as t

from xblock.core import XBlock
from xblock.completable import XBlockCompletionMode
from xblock.fields import Boolean, Float, Integer, List, Scope, String, Dict, Boolean
from xblockutils.settings import XBlockWithSettingsMixin
from xblockutils.resources import ResourceLoader
from xmodule.graders import ShowCorrectness
from web_fragments.fragment import Fragment
from common.djangoapps.xblock_django.constants import ATTR_KEY_USER_IS_STAFF

loader = ResourceLoader(__name__)
RE_COMBINE_WHITESPACE = re.compile(r"\s+")

_ = lambda text: text


class DummyTranslationService:
    """
    Dummy drop-in replacement for i18n XBlock service
    """
    gettext = _


class AnswersStat:

    def __init__(self, correct_answers: t.List[str], resp_answers: t.List[str], problem_weight=1,
                 grading_type='all_or_nothing'):
        self.correct_answers = correct_answers
        self.correct_answers_total_num = len(correct_answers)
        self.resp_answers = resp_answers
        self.grading_type = grading_type
        self.problem_weight = problem_weight if problem_weight and problem_weight >= 1 else 1
        self.user_correct_answers_num = 0
        self.percent_completion = 0
        self.weighted_percent_completion = 0
        self.user_correct_answers_num = sum([1 for ans in self.resp_answers if ans in self.correct_answers])

        if self.user_correct_answers_num > self.correct_answers_total_num:
            self.user_correct_answers_num = self.correct_answers_total_num

        if self.correct_answers_total_num > 0:
            if self.grading_type == 'all_or_nothing':
                if self.user_correct_answers_num == self.correct_answers_total_num:
                    self.percent_completion = 1
                    self.weighted_percent_completion = self.problem_weight
            elif self.grading_type == 'plus_minus':
                incorrect_answers_num = len(self.resp_answers) - self.user_correct_answers_num
                points = self.user_correct_answers_num - incorrect_answers_num
                if points < 0:
                    points = 0
                self.percent_completion = float(points) / self.correct_answers_total_num
                self.weighted_percent_completion = self.percent_completion * self.problem_weight
            elif self.correct_answers_total_num > 0:
                self.percent_completion = float(self.user_correct_answers_num) / self.correct_answers_total_num
                self.weighted_percent_completion = self.percent_completion * self.problem_weight

    def to_dict(self):
        return {
            'correct_answers': self.correct_answers,
            'resp_answers': self.resp_answers,
            'correct_answers_total_num': self.correct_answers_total_num,
            'grading_type': self.grading_type,
            'percent_completion': self.percent_completion,
            'problem_weight': self.problem_weight,
            'user_correct_answers_num': self.user_correct_answers_num,
            'weighted_percent_completion': self.weighted_percent_completion,
        }


@XBlock.wants('settings')
@XBlock.needs('i18n')
@XBlock.needs("user")
@XBlock.needs("user_state")
class TextHighlighterBlock(XBlockWithSettingsMixin, XBlock):
    display_name = String(
        display_name=_("Display Name"),
        help=_("Display Name"),
        scope=Scope.settings,
        default=_("Text Highlighter Block"),
    )

    description = String(
        display_name=_("Description"),
        help=_("Description"),
        scope=Scope.settings,
        default="Click to highlight the findings",
    )

    text = String(
        display_name=_("Text"),
        help=_("Text"),
        scope=Scope.settings,
        default="Some text",
    )

    use_tokenized_system = Boolean(
        default=False,
        scope=Scope.settings,
        help=_("Use Tokenized System for Highlighting")
    )

    correct_answers = List(
        display_name=_("Correct answers"),
        help=_("Correct answers"),
        scope=Scope.settings,
    )

    user_answers = List(
        default=None,
        scope=Scope.user_state,
        help=_("User answers")
    )

    non_limited_number_of_answers = Boolean(
        default=False,
        scope=Scope.settings,
        help=_("Allow non-limited number of answers")
    )

    grading_type = String(
        display_name=_("Grading Type"),
        help=_("Grading Type"),
        scope=Scope.settings,
        default='all_or_nothing',
    )

    weight = Float(
        display_name=_("Problem Weight"),
        help=_("Problem Weight"),
        scope=Scope.settings,
        default=1,
        values={"min": 1, "step": .01}
    )

    display_correct_answers_after_response = Boolean(
        default=True,
        scope=Scope.settings,
        help=_("Display Correct Answers After Response")
    )

    attempts = Integer(
        help=_("Number of attempts taken by the student on this problem"),
        default=0,
        scope=Scope.user_state
    )

    max_attempts_number = Integer(
        display_name=_("Maximum Attempts"),
        help=_("Maximum Attempts"),
        scope=Scope.settings,
        default=1,
        values={"min": 0, "step": 1}
    )

    block_settings_key = 'text-highlighter'
    has_score = True
    has_author_view = True
    completion_mode = XBlockCompletionMode.COMPLETABLE
    show_in_read_only_mode = True

    @property
    def course_id(self):
        return self.xmodule_runtime.course_id  # pylint: disable=no-member

    def max_score(self):  # pylint: disable=no-self-use
        """
        Returns the maximum score that can be achieved (always 1.0 on this XBlock)
        """
        return 1.0

    @property
    def i18n_service(self):
        """ Obtains translation service """
        i18n_service = self.runtime.service(self, "i18n")
        if i18n_service:
            return i18n_service
        else:
            return DummyTranslationService()

    def _prepare_text(self, text):
        if self.use_tokenized_system:
            return text.replace('<token>', '<span class="th-cl-token">').replace('</token>', '</span>')
        return text

    def _create_fragment(self, template, js_url=None, initialize_js_func=None):
        fragment = Fragment()
        fragment.add_content(template)
        if initialize_js_func:
            fragment.initialize_js(initialize_js_func, {})
        if js_url:
            fragment.add_javascript_url(self.runtime.local_resource_url(self, js_url))
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/th_block.css'))
        return fragment

    def get_real_user(self):
        anonymous_user_id = self.xmodule_runtime.anonymous_student_id
        user = self.xmodule_runtime.get_real_user(anonymous_user_id)
        return user

    def correctness_available(self):
        """
        Is the user allowed to see whether she's answered correctly?

        Limits access to the correct/incorrect flags, messages, and problem score.
        """
        if not self.display_correct_answers_after_response:
            return False
        user_is_staff = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        return ShowCorrectness.correctness_available(
            show_correctness=self.show_correctness,
            due_date=self.close_date,
            has_staff_access=user_is_staff,
        )

    def get_grade_text(self, ans_stat: AnswersStat, correctness_available=True):
        if ans_stat.correct_answers_total_num == 0:
            return ""
        postfix = f"({'graded' if self.graded else 'ungraded'}"
        if not correctness_available:
            postfix += ",  results hidden"
        postfix += ")"
        if not correctness_available:
            return f"{float(round(ans_stat.problem_weight, 2))} " \
                   f"{'points' if ans_stat.problem_weight > 1 else 'point'} possible {postfix}"
        return f"{float(round(ans_stat.weighted_percent_completion, 2))}/{float(round(ans_stat.problem_weight, 2))} " \
               f"{'points' if ans_stat.problem_weight > 1 else 'point'} {postfix}"

    def get_attempts_text(self, attempts):
        if attempts > 0 and self.max_attempts_number > 0:
            return f"You have used {attempts} of {self.max_attempts_number} attempt{'s' if self.max_attempts_number > 1 else ''}. "
        return ""

    def should_display_reset_button(self, selected_texts, ans_stat, attempts):
        return selected_texts and (ans_stat.percent_completion < 1.0 or not self.correctness_available()) \
            and (self.max_attempts_number == 0 or attempts < self.max_attempts_number)

    def student_view(self, context=None):
        is_studio_view = True if context and context.get("studio_view", False) else False
        correct_answers = self.correct_answers
        selected_texts = sorted(self.user_answers) if self.user_answers else []
        correctness_available = self.correctness_available()
        ans_stat = AnswersStat(correct_answers, selected_texts, self.weight, self.grading_type)
        attempts = 0
        if self.attempts > 0:
            attempts = self.attempts
        # backward compatibility for the case if self.attempts == 0 but answer was saved
        elif self.user_answers:
            attempts = 1

        context_dict = {
            'display_name': self.display_name,
            'text': self._prepare_text(self.text),
            'selected_texts': ", ".join(selected_texts) if selected_texts and not is_studio_view else "",
            'selected_texts_json': json.dumps(selected_texts) if selected_texts and not is_studio_view else "",
            'correct_answers_texts': ", ".join(correct_answers)if correct_answers else "",
            'is_studio_view': is_studio_view,
            'correct_answers_num': len(correct_answers),
            'description': self.description,
            'percent_completion': ans_stat.percent_completion,
            'weighted_percent_completion': ans_stat.weighted_percent_completion,
            'user_correct_answers_num': ans_stat.user_correct_answers_num,
            'correct_answers_total_num': ans_stat.correct_answers_total_num,
            'problem_weight': self.weight,
            'graded': self.graded,
            'grade_text': self.get_grade_text(ans_stat, correctness_available),
            'correctness_available': correctness_available,
            'use_tokenized_system': self.use_tokenized_system,
            'non_limited_number_of_answers': self.non_limited_number_of_answers,
            'attempts': attempts,
            'max_attempts_number': self.max_attempts_number,
            'attempts_text': self.get_attempts_text(attempts),
            'display_reset_button': self.should_display_reset_button(selected_texts, ans_stat, attempts)
        }
        template = loader.render_django_template("/templates/public.html", context=context_dict,
                                                 i18n_service=self.i18n_service)
        return self._create_fragment(template, js_url='public/js/th_public.js',
                                     initialize_js_func='TextHighlighterBlock')

    def author_view(self, context=None):
        return self.student_view({"studio_view": True})

    def studio_view(self, context=None):
        context_dict = {
            'display_name': self.display_name,
            'text': self.text,
            'correct_answers': "\n".join(self.correct_answers) if self.correct_answers else "",
            'description': self.description,
            'grading_type': self.grading_type,
            'problem_weight': self.weight,
            'use_tokenized_system': self.use_tokenized_system,
            'non_limited_number_of_answers': self.non_limited_number_of_answers,
            'display_correct_answers_after_response': self.display_correct_answers_after_response,
            'max_attempts_number': self.max_attempts_number,
        }
        template = loader.render_django_template("/templates/staff.html", context=context_dict,
                                                 i18n_service=self.i18n_service)
        return self._create_fragment(template, js_url='public/js/th_staff.js',
                                     initialize_js_func='TextHighlighterEditBlock')

    def _prepare_answers_list(self, answers_list_raw: t.List[str]) -> t.List[str]:
        answers_list_res = []
        for answer in answers_list_raw:
            if answer:
                answer_corrected = RE_COMBINE_WHITESPACE.sub(" ", answer).strip()
                if answer_corrected:
                    answers_list_res.append(answer_corrected)
        return sorted(list(set(answers_list_res)))

    @XBlock.json_handler
    def update_editor_context(self, data, suffix=''):  # pylint: disable=unused-argument
        from bs4 import BeautifulSoup

        display_name = data.get('display_name')
        if not display_name:
            return {
                'result': 'error',
                'msg': self.i18n_service.gettext('Display Name is not set')
            }

        text = data.get('text')
        if not text:
            return {
                'result': 'error',
                'msg': self.i18n_service.gettext('Text is not set')
            }

        correct_answers = data.get('correct_answers', '')
        correct_answers_list_tmp = correct_answers.split("\n")
        correct_answers_list_res = self._prepare_answers_list(correct_answers_list_tmp)
        if not correct_answers_list_res:
            return {
                'result': 'error',
                'msg': self.i18n_service.gettext('Correct answers are not set')
            }

        description = data.get('description')
        if description:
            description = description.strip()

        grading_type = data.get('grading_type')
        if grading_type not in ['all_or_nothing', 'partial_credit', 'plus_minus']:
            return {
                'result': 'error',
                'msg': self.i18n_service.gettext('Invalid grading type')
            }

        problem_weight = data.get('problem_weight')

        if not problem_weight:
            problem_weight = 1
        try:
            problem_weight = float(problem_weight)
        except (ValueError, TypeError):
            problem_weight = 1
        if problem_weight < 1:
            problem_weight = 1

        display_correct_answers_after_response = data.get('display_correct_answers_after_response')
        use_tokenized_system = data.get('use_tokenized_system')
        non_limited_number_of_answers = data.get('non_limited_number_of_answers')
        max_attempts_number = data.get('max_attempts_number')

        try:
            max_attempts_number = int(max_attempts_number)
        except (ValueError, TypeError):
            max_attempts_number = 1

        if use_tokenized_system:
            if '<token>' not in text.lower():
                return {
                    'result': 'error',
                    'msg': self.i18n_service.gettext('Please, use at least one "token" in text')
                }
            html_block = BeautifulSoup(text)
            tokens = html_block.find_all('token')
            token_answers = [token.string.strip() for token in tokens]
            for ca in correct_answers_list_res:
                if ca not in token_answers:
                    return {
                        'result': 'error',
                        'msg': 'Answer "%s" must be framed with "token" tag' % ca
                    }

        self.display_name = display_name
        self.text = text
        self.correct_answers = correct_answers_list_res
        self.description = description
        self.grading_type = grading_type
        self.weight = problem_weight
        self.display_correct_answers_after_response = bool(display_correct_answers_after_response)
        self.use_tokenized_system = bool(use_tokenized_system)
        self.non_limited_number_of_answers = bool(non_limited_number_of_answers)
        self.max_attempts_number = max_attempts_number

        return {
            'result': 'success'
        }

    @XBlock.json_handler
    def publish_answers(self, data, suffix=''):
        try:
            resp_answers_raw = data.pop('answers')
        except KeyError:
            return {'result': 'error', 'message': "Invalid data"}

        if not isinstance(resp_answers_raw, list):
            return {'result': 'error', 'message': "Invalid answers format"}

        resp_answers = self._prepare_answers_list(resp_answers_raw)
        correct_answers = self.correct_answers
        correctness_available = self.correctness_available()

        ans_stat = AnswersStat(correct_answers, resp_answers, self.weight, self.grading_type)

        # backward compatibility for the case if self.attempts == 0 but answer was saved
        if self.user_answers and self.attempts == 0:
            self.attempts = 1
        self.attempts = self.attempts + 1
        self.user_answers = resp_answers

        self.runtime.publish(self, 'progress', {})
        self.runtime.publish(self, 'grade', {
            'value': ans_stat.percent_completion,
            'max_value': 1,
        })

        event_type = 'xblock.text-highlighter.new_submission'
        data['user_id'] = self.scope_ids.user_id
        data['correct_answers'] = correct_answers
        data['user_answers'] = resp_answers
        data['new_attempt'] = True
        data['percent_completion'] = float(round(ans_stat.percent_completion, 2))
        data['weighted_percent_completion'] = float(round(ans_stat.weighted_percent_completion, 2))
        data['max_grade'] = 1
        data['weight'] = self.weight
        data['grading_type'] = self.grading_type
        data['display_name'] = self.display_name
        data['description'] = self.description
        data['text'] = self.text
        data['correct_answers'] = self.correct_answers
        data['user_answers'] = self.user_answers
        data['correctness_available'] = correctness_available
        data['attempts'] = self.attempts
        data['max_attempts_number'] = self.max_attempts_number

        self.runtime.publish(self, event_type, data)

        return {
            'result': 'success',
            'selected_texts': ", ".join(resp_answers) if resp_answers else "",
            'correct_answers_texts': ", ".join(correct_answers) if correct_answers else "",
            'user_correct_answers_num': ans_stat.user_correct_answers_num,
            'correct_answers_total_num': ans_stat.correct_answers_total_num,
            'percent_completion': ans_stat.percent_completion,
            'weighted_percent_completion': ans_stat.weighted_percent_completion,
            'problem_weight': self.weight,
            'graded': self.graded,
            'grade_text': self.get_grade_text(ans_stat, correctness_available),
            'attempts': self.attempts,
            'attempts_text': self.get_attempts_text(self.attempts),
            'max_attempts_number': self.max_attempts_number,
            'display_reset_button': self.should_display_reset_button(True, ans_stat, self.attempts)
        }

    @XBlock.json_handler
    def reset_answers(self, data, suffix=''):
        correct_answers = self.correct_answers
        correctness_available = self.correctness_available()
        ans_stat = AnswersStat(correct_answers, [], self.weight, self.grading_type)

        # backward compatibility for the case if self.attempts == 0 but answer was saved
        if self.user_answers and self.attempts == 0:
            self.attempts = 1
        self.user_answers = []
        self.runtime.publish(self, 'progress', {})
        self.runtime.publish(self, 'grade', {
            'value': ans_stat.percent_completion,
            'max_value': 1,
        })

        event_type = 'xblock.text-highlighter.reset_submission'
        data['user_id'] = self.scope_ids.user_id
        data['correct_answers'] = correct_answers
        data['user_answers'] = []
        data['new_attempt'] = True
        data['percent_completion'] = float(round(ans_stat.percent_completion, 2))
        data['weighted_percent_completion'] = float(round(ans_stat.weighted_percent_completion, 2))
        data['max_grade'] = 1
        data['weight'] = self.weight
        data['grading_type'] = self.grading_type
        data['display_name'] = self.display_name
        data['description'] = self.description
        data['text'] = self.text
        data['correct_answers'] = self.correct_answers
        data['user_answers'] = self.user_answers
        data['correctness_available'] = correctness_available
        data['attempts'] = self.attempts
        data['max_attempts_number'] = self.max_attempts_number

        self.runtime.publish(self, event_type, data)

        return {
            'grade_text': self.get_grade_text(ans_stat, correctness_available),
        }
