import json
import tokenize
from pprint import pprint
from typing import List

import emoji

from custemoji import Emoji

MAX_LINE_CHARACTERS = 31

def results_list(args, prefix=''):
    # TODO make this method recursive
    result = '```'
    memo = emoji.emojize(':memo:', use_aliases=True)
    if isinstance(args, dict):
        for value in args.values():
            if isinstance(value, list):
                # TODO: doesnt work
                for s in value:
                    result += '\n{} {}'.format(memo, s)
            else:
                result += '\n{} {}'.format(memo, value)

    result += '```'
    return result


def centered(text):
    result = '\n'.join([line.center(MAX_LINE_CHARACTERS) for line in text.splitlines()])
    return result


def success(text):
    return '{} {}'.format(Emoji.WHITE_HEAVY_CHECK_MARK, text, hide_keyboard=True)


def failure(text):
    return '{} {}'.format(Emoji.CROSS_MARK, text)


def action_hint(text):
    return '{} {}'.format(Emoji.THOUGHT_BALLOON, text)


def none_action(text):
    return '{} {}'.format(Emoji.NEGATIVE_SQUARED_CROSS_MARK, text)


if __name__ == '__main__':
    # test = json.loads('{"category": ["Miscellaneous"], \
    #            "intro_es": "Spanish intro sent", \
    # "new_bots_list": "List of new bots sent"}')
    # print(results_list(test))
    print(centered('• @botlist •\n03-02-2017'))
