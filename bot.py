# -*- coding: utf-8 -*-
import binascii
import datetime
import json
import logging
import os
import re
import sys
import traceback
from pprint import pprint

import emoji
from telegram import ForceReply
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import ParseMode
from telegram import ReplyKeyboardMarkup
from telegram import ReplyKeyboardRemove
from telegram.ext import CallbackQueryHandler
from telegram.ext import ChosenInlineResultHandler
from telegram.ext import MessageHandler, \
    Filters, RegexHandler, InlineQueryHandler, ConversationHandler
from telegram.ext import Updater, CommandHandler

import appglobals
import captions
from components import botlist
import const
import helpers
import search
import util
from components import botlistchat
from components import botproperties
from components import admin
from components import basic
from components import botlistchat
from components import botproperties
from components import contributions
from components import eastereggs
from components import favorites
from components import help
from components import inlinequery
from const import BotStates, CallbackActions, CallbackStates
from dialog import messages
from lib import InlineCallbackButton
from lib import InlineCallbackHandler
from model import Category, Bot, Country
from model import Favorite
from model import Keyword
from model import Notifications
from model.suggestion import Suggestion
from model.user import User
from pwrtelegram import PWRTelegram
from util import private_chat_only, track_groups

logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)


def manage_subscription(bot, update):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if util.is_group_message(update):
        admins = bot.get_chat_administrators(chat_id)
        if user_id not in admins:
            util.send_message_failure(bot, chat_id, "Sorry, but only Administrators of this group are allowed "
                                                    "to manage subscriptions.")
            return

    msg = "Would you like to be notified when new bots arrive at the @BotList?"
    buttons = [[
        InlineKeyboardButton(util.success("Yes"),
                             callback_data=util.callback_for_action(CallbackActions.SET_NOTIFICATIONS,
                                                                    {'value': True})),
        InlineKeyboardButton("No", callback_data=util.callback_for_action(CallbackActions.SET_NOTIFICATIONS,
                                                                          {'value': False}))]]
    reply_markup = InlineKeyboardMarkup(buttons)
    util.send_md_message(bot, chat_id, msg, reply_markup=reply_markup)
    return ConversationHandler.END


def _new_bots_text():
    new_bots = Bot.get_new_bots()
    if len(new_bots) > 0:
        txt = "Fresh new bots from the last {} days 💙:\n\n{}".format(
            const.BOT_CONSIDERED_NEW,
            Bot.get_new_bots_str())
    else:
        txt = 'No new bots available.'
    return txt


@track_groups
def all_handler(bot, update, chat_data):
    chat_id = update.effective_chat.id
    if update.message and update.message.new_chat_members:
        if int(const.SELF_BOT_ID) in update.message.new_chat_members.id:
            # bot was added to a group
            basic.start(bot, update, chat_data)
    return ConversationHandler.END


def search_query(bot, update, chat_data, query, send_errors=True):
    chat_id = update.effective_chat.id
    results = search.search_bots(query)
    is_admin = chat_id in const.MODERATORS
    reply_markup = ReplyKeyboardMarkup(
        basic.main_menu_buttons(is_admin), resize_keyboard=True
    ) if util.is_private_message(update) else ReplyKeyboardRemove()
    if results:
        if len(results) == 1:
            return send_bot_details(bot, update, chat_data, results[0])
        too_many_results = len(results) > const.MAX_SEARCH_RESULTS

        bots_list = ''
        if chat_id in const.MODERATORS:
            # append edit buttons
            bots_list += '\n'.join(["{} — /edit{} 🛃".format(b, b.id) for b in list(results)[:100]])
        else:
            bots_list += '\n'.join([str(b) for b in list(results)[:const.MAX_SEARCH_RESULTS]])
        bots_list += '\n…' if too_many_results else ''
        bots_list = messages.SEARCH_RESULTS.format(bots=bots_list, num_results=len(results),
                                                   plural='s' if len(results) > 1 else '',
                                                   query=query)
        msg = update.message.reply_text(bots_list, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        if send_errors:
            msg = update.message.reply_text(
                util.failure("Sorry, I couldn't find anything related "
                             "to *{}* in the @BotList. /search".format(util.escape_markdown(query))),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup)
    return ConversationHandler.END


def search_handler(bot, update, chat_data, args=None):
    if args:
        search_query(bot, update, chat_data, ' '.join(args))
    else:
        # no search term
        if util.is_group_message(update):
            action = const.DeepLinkingActions.SEARCH
            update.message.reply_text(
                "Please use the search command with arguments, inlinequeries or continue in private. "
                "Example: `/search awesome bot`",
                quote=True,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton('🔎 Search inline', switch_inline_query_current_chat=''),
                        InlineKeyboardButton(captions.SWITCH_PRIVATE,
                                             url="https://t.me/{}?start={}".format(
                                                 const.SELF_BOT_NAME,
                                                 action))
                    ]]
                ))
        else:
            update.message.reply_text(messages.SEARCH_MESSAGE,
                                      reply_markup=ForceReply(selective=True))
    return ConversationHandler.END


def _select_category_buttons(callback_action=None):
    if callback_action is None:
        # set default
        callback_action = CallbackActions.SELECT_BOT_FROM_CATEGORY
    categories = Category.select().order_by(Category.name.asc()).execute()

    buttons = util.build_menu([InlineKeyboardButton(
        '{}{}'.format(emoji.emojize(c.emojis, use_aliases=True), c.name),
        callback_data=util.callback_for_action(
            callback_action, {'id': c.id})) for c in categories], 2)
    buttons.insert(0, [InlineKeyboardButton(
        '🆕 New Bots', callback_data=util.callback_for_action(CallbackActions.NEW_BOTS_SELECTED))])
    return buttons


@track_groups
def select_category(bot, update, chat_data, callback_action=None):
    chat_id = update.effective_chat.id
    reply_markup = InlineKeyboardMarkup(_select_category_buttons(callback_action))
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = util.send_or_edit_md_message(bot, chat_id, util.action_hint(messages.SELECT_CATEGORY),
                                       to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup)
    callback(msg)
    return ConversationHandler.END


def access_token(bot, update):
    update.message.reply_text(binascii.hexlify(os.urandom(32)).decode('utf-8'))
    return ConversationHandler.END


def credits(bot, update):
    users_contrib = User.select().join()
    pass
    Bot.select(Bot.submitted_by)
    return ConversationHandler.END


def t3chnostats(bot, update):
    days = 30
    txt = 'Bots approved by other people *in the last {} days*:\n\n'.format(days)
    bots = Bot.select().where(
        (Bot.approved_by != User.get(User.chat_id == 918962)) &
        (Bot.date_added.between(
            datetime.date.today() - datetime.timedelta(days=days),
            datetime.date.today()
        ))
    )
    txt += '\n'.join(['{} by @{}'.format(str(b), b.approved_by.username) for b in bots])
    update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


def show_new_bots(bot, update, chat_data, back_button=False):
    chat_id = update.effective_chat.id
    channel = helpers.get_channel()
    reply_markup = None
    if back_button:
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(captions.BACK, callback_data=util.callback_for_action(
                CallbackActions.SELECT_CATEGORY
            )),
            InlineKeyboardButton("Show in BotList",
                                 url="http://t.me/{}/{}".format(channel.username, channel.new_bots_mid)),
            InlineKeyboardButton("Share", switch_inline_query=messages.NEW_BOTS_INLINEQUERY)
        ]])
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = util.send_or_edit_md_message(bot, chat_id, _new_bots_text(), to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup, reply_to_message_id=util.mid_from_update(update))
    callback(msg)
    return ConversationHandler.END


def send_category(bot, update, chat_data, category=None):
    uid = util.uid_from_update(update)
    cid = update.effective_chat.id
    bots = Bot.of_category(category)[:const.MAX_BOTS_PER_MESSAGE]
    bots_with_description = [b for b in bots if b.description is not None]
    detailed_buttons_enabled = len(bots_with_description) > 0 and util.is_private_message(update)

    callback = CallbackActions.SEND_BOT_DETAILS

    if detailed_buttons_enabled:
        buttons = [InlineKeyboardButton(x.username, callback_data=util.callback_for_action(
            callback, {'id': x.id})) for x in bots_with_description]
    else:
        buttons = []
    menu = util.build_menu(buttons, 2)
    menu.insert(0, [
        InlineKeyboardButton(captions.BACK, callback_data=util.callback_for_action(
            CallbackActions.SELECT_CATEGORY
        )),
        InlineKeyboardButton("Show in BotList", url='http://t.me/botlist/{}'.format(category.current_message_id)),
        InlineKeyboardButton("Share", switch_inline_query=category.name)
    ])
    txt = "There are *{}* bots in the category *{}*:\n\n".format(len(bots), str(category))

    if uid in const.MODERATORS and util.is_private_message(update):
        # append admin edit buttons
        txt += '\n'.join(["{} — /edit{} 🛃".format(b, b.id) for b in bots])
    else:
        txt += '\n'.join([str(b) for b in bots])

    if detailed_buttons_enabled:
        txt += "\n\n" + util.action_hint("Press a button below to get a detailed description.")

    reply_markup = InlineKeyboardMarkup(menu)
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = util.send_or_edit_md_message(bot, cid, txt, to_edit=util.mid_from_update(update), reply_markup=reply_markup)
    callback(msg)


@private_chat_only
def send_bot_details(bot, update, chat_data, item=None):
    uid = util.uid_from_update(update)
    user = User.from_update(update)
    first_row = list()

    if item is None:
        try:
            text = update.message.text
            bot_in_text = re.findall(const.REGEX_BOT_IN_TEXT, text)[0]
            item = Bot.by_username(bot_in_text)

        # except (AttributeError, Bot.DoesNotExist):
        except Bot.DoesNotExist:
            update.message.reply_text(util.failure(
                "This bot is not in the @BotList. If you think this is a mistake, see the /examples for /contributing."))
            return

    if item.approved:
        # bot is already in the botlist => show information
        txt = item.detail_text
        if item.description is None and not Keyword.select().where(Keyword.entity == item).exists():
            txt += ' is in the @BotList.'
        btn = InlineCallbackButton(captions.BACK_TO_CATEGORY, CallbackActions.SELECT_BOT_FROM_CATEGORY,
                                   {'id': item.category.id})
        # btn = InlineKeyboardButton(captions.BACK_TO_CATEGORY, callback_data=util.callback_for_action(
        #     CallbackActions.SELECT_BOT_FROM_CATEGORY, {'id': item.category.id}
        # ))
        first_row.insert(0, btn)
        first_row.append(InlineKeyboardButton(captions.SHARE, switch_inline_query=item.username))

        if uid in const.MODERATORS:
            first_row.append(InlineKeyboardButton(
                "🛃 Edit", callback_data=util.callback_for_action(
                    CallbackActions.EDIT_BOT,
                    {'id': item.id}
                )))
    else:
        txt = '{} is currently pending to be accepted for the @BotList.'.format(item)
        if uid in const.MODERATORS:
            first_row.append(InlineKeyboardButton(
                "🛃 Accept / Reject", callback_data=util.callback_for_action(
                    CallbackActions.APPROVE_REJECT_BOTS,
                    {'id': item.id}
                )))
            # first_row.append(InlineKeyboardButton(
            #     util.success("🛃 Reject"), callback_data=util.callback_for_action(
            #         CallbackActions.ACCEPT_BOT,
            #         {'id': item.id}
            #     )))

    buttons = [first_row]
    favs = [f.bot for f in Favorite.select_all(user)]
    if item not in favs:
        buttons.append([
            InlineKeyboardButton(captions.ADD_TO_FAVORITES,
                                 callback_data=util.callback_for_action(CallbackActions.ADD_TO_FAVORITES,
                                                                        {'id': item.id}))
        ])
    reply_markup = InlineKeyboardMarkup(buttons)
    reply_markup, callback = botlistchat.append_delete_button(update, chat_data, reply_markup)
    msg = util.send_or_edit_md_message(bot, uid,
                                       txt,
                                       to_edit=util.mid_from_update(update),
                                       reply_markup=reply_markup
                                       )
    callback(msg)
    return CallbackStates.SHOWING_BOT_DETAILS


def set_notifications(bot, update, value: bool):
    chat_id = update.effective_chat.id
    try:
        notifications = Notifications.get(Notifications.chat_id == chat_id)
    except Notifications.DoesNotExist:
        notifications = Notifications(chat_id=chat_id)
    notifications.enabled = value
    notifications.save()

    msg = util.success("Nice! Notifications enabled.") if value else "Ok, notifications disabled."
    msg += '\nYou can always adjust this setting with the /subscribe command.'
    util.send_or_edit_md_message(bot, chat_id, msg, to_edit=util.mid_from_update(update))
    return ConversationHandler.END


def bot_checker_job(bot, job):
    pwt = PWRTelegram('your_token')
    bots = Bot.select()
    for b in bots:
        print('Sending /start to {}...'.format(b.username))
        msg = pwt.send_message(b.username, '/start')
        print('Awaiting response...')
        if msg:
            resp = pwt.await_response(msg)
            if resp:
                print('{} answered.'.format(b.username))
            else:
                print('{} is offline.'.format(b.username))
        else:
            print('Could not contact {}.'.format(b.username))


def forward_router(bot, update, chat_data):
    text = update.message.text
    uid = util.uid_from_update(update)

    # match first username in forwarded message
    try:
        username = re.match(const.REGEX_BOT_IN_TEXT, text).groups()[0]
        if username == '@' + const.SELF_BOT_NAME:
            return  # ignore

        item = Bot.get(Bot.username == username)

        send_bot_details(bot, update, chat_data, item)

    except (AttributeError, Bot.DoesNotExist):
        pass  # no valid username in forwarded message


def reply_router(bot, update, chat_data):
    text = update.message.reply_to_message.text

    if text == messages.ADD_FAVORITE:
        query = update.message.text
        favorites.add_favorite_handler(bot, update, query)
    elif text == messages.SEARCH_MESSAGE:
        query = update.message.text
        search_query(bot, update, chat_data, query)

    # BOTPROPERTIES
    bot_properties = ['description', 'extra', 'name', 'username']
    partition = text.partition(messages.BOTPROPERTY_STARTSWITH)
    if partition[1] != '':
        bot_property = next(p for p in bot_properties if partition[2].startswith(p))
        # Reply for setting a bot property
        botproperties.set_text_property(bot, update, chat_data, bot_property)

    elif text == messages.BAN_MESSAGE:
        query = update.message.text
        admin.ban_handler(bot, update, query, True)
    elif text == messages.UNBAN_MESSAGE:
        query = update.message.text
        admin.ban_handler(bot, update, query, False)


def callback_router(bot, update, chat_data, job_queue):
    obj = json.loads(str(update.callback_query.data))
    user = User.from_update(update)

    try:
        if 'a' in obj:
            action = obj['a']

            # BOTLISTCHAT
            if action == CallbackActions.DELETE_CONVERSATION:
                botlistchat.delete_conversation(bot, update, chat_data)
            # HELP
            if action == CallbackActions.HELP:
                help.help(bot, update)
            if action == CallbackActions.CONTRIBUTING:
                help.contributing(bot, update)
            if action == CallbackActions.EXAMPLES:
                help.examples(bot, update)
            # BASIC QUERYING
            if action == CallbackActions.SELECT_CATEGORY:
                select_category(bot, update, chat_data)
            if action == CallbackActions.SELECT_BOT_FROM_CATEGORY:
                category = Category.get(id=obj['id'])
                send_category(bot, update, chat_data, category)
            if action == CallbackActions.SEND_BOT_DETAILS:
                item = Bot.get(id=obj['id'])
                send_bot_details(bot, update, chat_data, item)
            # FAVORITES
            if action == CallbackActions.TOGGLE_FAVORITES_LAYOUT:
                value = obj['v']
                favorites.toggle_favorites_layout(bot, update, value)
            if action == CallbackActions.ADD_FAVORITE:
                favorites.add_favorite_handler(bot, update)
            if action == CallbackActions.REMOVE_FAVORITE_MENU:
                favorites.remove_favorite_menu(bot, update)
            if action == CallbackActions.REMOVE_FAVORITE:
                to_remove = Favorite.get(id=obj['id'])
                to_remove.delete_instance()
                favorites.remove_favorite_menu(bot, update)
            if action == CallbackActions.SEND_FAVORITES_LIST:
                favorites.send_favorites_list(bot, update)
            if action == CallbackActions.ADD_ANYWAY:
                favorites.add_custom(bot, update, obj['u'])
            if action == CallbackActions.ADD_TO_FAVORITES:
                discreet = obj.get('discreet', False)
                item = Bot.get(id=obj['id'])
                favorites.add_favorite(bot, update, item, callback_alert=discreet)
            # ACCEPT/REJECT BOT SUBMISSIONS
            if action == CallbackActions.APPROVE_REJECT_BOTS:
                custom_approve_list = [Bot.get(id=obj['id'])]
                admin.approve_bots(bot, update, override_list=custom_approve_list)
            if action == CallbackActions.ACCEPT_BOT:
                to_accept = Bot.get(id=obj['id'])
                admin.edit_bot_category(bot, update, to_accept, CallbackActions.BOT_ACCEPTED)
                # Run in x minutes, giving the moderator enough time to edit bot details
                job_queue.run_once(lambda b, job:
                                   botlistchat.notify_group_submission_accepted(b, job, to_accept),
                                   const.BOT_ACCEPTED_IDLE_TIME * 60)
            if action == CallbackActions.REJECT_BOT:
                to_reject = Bot.get(id=obj['id'])
                notification = obj.get('ntfc', True)
                admin.reject_bot_submission(bot, update, to_reject, verbose=False, notify_submittant=notification)
                admin.approve_bots(bot, update, obj['page'])
            if action == CallbackActions.BOT_ACCEPTED:
                to_accept = Bot.get(id=obj['bid'])
                category = Category.get(id=obj['cid'])
                admin.accept_bot_submission(bot, update, to_accept, category)
            if action == CallbackActions.COUNT_THANK_YOU:
                accepted = Bot.get(id=obj['id'])
                new_count = obj['count']
                contributions.count_thank_you(bot, update, accepted, new_count)
            # ADD BOT
            # if action == CallbackActions.ADD_BOT_SELECT_CAT:
            #     category = Category.get(id=obj['id'])
            #     admin.add_bot(bot, update, chat_data, category)
            # EDIT BOT
            if action == CallbackActions.EDIT_BOT:
                to_edit = Bot.get(id=obj['id'])
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_SELECT_CAT:
                to_edit = Bot.get(id=obj['id'])
                admin.edit_bot_category(bot, update, to_edit)
            if action == CallbackActions.EDIT_BOT_CAT_SELECTED:
                to_edit = Bot.get(id=obj['bid'])
                cat = Category.get(id=obj['cid'])
                botproperties.change_category(bot, update, to_edit, cat)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_COUNTRY:
                to_edit = Bot.get(id=obj['id'])
                botproperties.set_country_menu(bot, update, to_edit)
            if action == CallbackActions.SET_COUNTRY:
                to_edit = Bot.get(id=obj['bid'])
                if obj['cid'] == 'None':
                    country = None
                else:
                    country = Country.get(id=obj['cid'])
                botproperties.set_country(bot, update, to_edit, country)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_DESCRIPTION:
                to_edit = Bot.get(id=obj['id'])
                botproperties.set_text_property(bot, update, chat_data, 'description', to_edit)
            if action == CallbackActions.EDIT_BOT_EXTRA:
                to_edit = Bot.get(id=obj['id'])
                botproperties.set_text_property(bot, update, chat_data, 'extra', to_edit)
            if action == CallbackActions.EDIT_BOT_NAME:
                to_edit = Bot.get(id=obj['id'])
                botproperties.set_text_property(bot, update, chat_data, 'name', to_edit)
            if action == CallbackActions.EDIT_BOT_USERNAME:
                to_edit = Bot.get(id=obj['id'])
                botproperties.set_text_property(bot, update, chat_data, 'username', to_edit)
            # if action == CallbackActions.EDIT_BOT_KEYWORDS:
            #     to_edit = Bot.get(id=obj['id'])
            #     botproperties.set_keywords_init(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_INLINEQUERIES:
                to_edit = Bot.get(id=obj['id'])
                value = bool(obj['value'])
                botproperties.toggle_value(bot, update, 'inlinequeries', to_edit, value)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_OFFICIAL:
                to_edit = Bot.get(id=obj['id'])
                value = bool(obj['value'])
                botproperties.toggle_value(bot, update, 'official', to_edit, value)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_OFFLINE:
                to_edit = Bot.get(id=obj['id'])
                value = bool(obj['value'])
                botproperties.toggle_value(bot, update, 'offline', to_edit, value)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.EDIT_BOT_SPAM:
                to_edit = Bot.get(id=obj['id'])
                value = bool(obj['value'])
                botproperties.toggle_value(bot, update, 'spam', to_edit, value)
                admin.edit_bot(bot, update, chat_data, to_edit)
            if action == CallbackActions.CONFIRM_DELETE_BOT:
                to_delete = Bot.get(id=obj['id'])
                botproperties.delete_bot_confirm(bot, update, to_delete)
            if action == CallbackActions.DELETE_BOT:
                to_edit = Bot.get(id=obj['id'])
                botproperties.delete_bot(bot, update, to_edit)
                send_category(bot, update, to_edit.category)
            if action == CallbackActions.ACCEPT_SUGGESTION:
                suggestion = Suggestion.get(id=obj['id'])
                suggestion.execute()
                admin.approve_suggestions(bot, update, page=obj['page'])
            if action == CallbackActions.REJECT_SUGGESTION:
                suggestion = Suggestion.get(id=obj['id'])
                suggestion.delete_instance()
                admin.approve_suggestions(bot, update, page=obj['page'])
            if action == CallbackActions.CHANGE_SUGGESTION:
                suggestion = Suggestion.get(id=obj['id'])
                botproperties.change_suggestion(bot, update, suggestion, page_handover=obj['page'])
            if action == CallbackActions.CHANGE_SUGGESTION_TEXT:
                suggestion = Suggestion.get(id=obj['id'])
                botproperties.change_suggestion_text(bot, update, suggestion, page_handover=obj['page'])
            if action == CallbackActions.SWITCH_SUGGESTIONS_PAGE:
                page = obj['page']
                admin.approve_suggestions(bot, update, page)
            if action == CallbackActions.SWITCH_APPROVALS_PAGE:
                admin.approve_bots(bot, update, page=obj['page'])
            if action == CallbackActions.SET_NOTIFICATIONS:
                set_notifications(bot, update, obj['value'])
            if action == CallbackActions.NEW_BOTS_SELECTED:
                show_new_bots(bot, update, chat_data, back_button=True)
            if action == CallbackActions.REMOVE_KEYWORD:
                to_edit = Bot.get(id=obj['id'])
                kw = Keyword.get(id=obj['kwid'])
                kw.delete_instance()
                botproperties.set_keywords(bot, update, chat_data, to_edit)
            if action == CallbackActions.ABORT_SETTING_KEYWORDS:
                to_edit = Bot.get(id=obj['id'])
                admin.edit_bot(bot, update, chat_data, to_edit)
            # SEND BOTLIST
            if action == CallbackActions.SEND_BOTLIST:
                silent = obj.get('silent', False)
                re_send = obj.get('re', False)
                botlist.send_botlist(bot, update, resend=re_send, silent=silent)
            if action == CallbackActions.RESEND_BOTLIST:
                botlist.send_botlist(bot, update, resend=True)
    except Exception as e:
        traceback.print_exc()
        bot.sendMessage(bot, const.ADMINS[0], "Exception for {}:\n{}".format(user, e))
    finally:
        bot.answerCallbackQuery(update.callback_query.id)
        return ConversationHandler.END


# def select_language(bot, update):
#     chat_id = update.effective_chat.id
#     msg = util.action_hint("Choose a language")
#     buttons = [[
#         InlineKeyboardButton("🇬🇧 English",
#                              callback_data=util.callback_for_action(CallbackActions.SELECT_LANGUAGE,
#                                                                     {'lang': 'en'})),
#         InlineKeyboardButton("🇪🇸 Spanish", callback_data=util.callback_for_action(CallbackActions.SELECT_LANGUAGE,
#                                                                                     {'lang': 'es'}))]]
#     reply_markup = InlineKeyboardMarkup(buttons)
#     util.send_md_message(bot, chat_id, msg, reply_markup=reply_markup)
#     return ConversationHandler.END


def main():
    # #TODO Start BotList API
    # thread = Thread(target=botlistapi.start_server)
    # thread.start()

    try:
        BOT_TOKEN = str(os.environ['TG_TOKEN'])
    except Exception:
        BOT_TOKEN = str(sys.argv[1])

    updater = Updater(BOT_TOKEN, workers=5)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[
            InlineCallbackHandler(CallbackActions.EDIT_BOT_KEYWORDS,
                                  botproperties.set_keywords_init,
                                  serialize=lambda data: dict(to_edit=Bot.get(id=data['id'])),
                                  pass_chat_data=True)
        ],
        states={
            BotStates.SENDING_KEYWORDS: [
                MessageHandler(Filters.text, botproperties.add_keyword, pass_chat_data=True),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(callback_router, pass_chat_data=True, pass_job_queue=True)
        ],
        per_chat=True,
        allow_reentry=False
    )
    dp.add_handler(conv_handler)
    dp.add_handler(CallbackQueryHandler(callback_router, pass_chat_data=True, pass_job_queue=True))
    dp.add_handler(CommandHandler('category', select_category, pass_chat_data=True))
    dp.add_handler(CommandHandler('categories', select_category, pass_chat_data=True))
    dp.add_handler(CommandHandler('cat', select_category, pass_chat_data=True))
    dp.add_handler(CommandHandler('search', search_handler, pass_args=True, pass_chat_data=True))
    dp.add_handler(CommandHandler('s', search_handler, pass_args=True, pass_chat_data=True))

    dp.add_handler(MessageHandler(Filters.reply, reply_router, pass_chat_data=True))
    dp.add_handler(MessageHandler(Filters.forwarded, forward_router, pass_chat_data=True))

    dp.add_handler(CommandHandler("admin", admin.menu))
    dp.add_handler(CommandHandler("a", admin.menu))

    # admin menu
    dp.add_handler(RegexHandler(captions.APPROVE_BOTS + '.*', admin.approve_bots))
    dp.add_handler(RegexHandler(captions.APPROVE_SUGGESTIONS + '.*', admin.approve_suggestions))
    dp.add_handler(RegexHandler(captions.SEND_BOTLIST, admin.prepare_transmission, pass_chat_data=True))
    dp.add_handler(RegexHandler(captions.SEND_CONFIG_FILES, admin.send_config_files))
    dp.add_handler(RegexHandler(captions.FIND_OFFLINE, admin.send_offline))

    # main menu
    dp.add_handler(RegexHandler(captions.ADMIN_MENU, admin.menu))
    dp.add_handler(RegexHandler(captions.REFRESH, admin.menu))
    dp.add_handler(RegexHandler(captions.CATEGORIES, select_category, pass_chat_data=True))
    dp.add_handler(RegexHandler(captions.FAVORITES, favorites.send_favorites_list))
    dp.add_handler(RegexHandler(captions.NEW_BOTS, show_new_bots, pass_chat_data=True))
    dp.add_handler(RegexHandler(captions.SEARCH, search_handler, pass_chat_data=True))
    dp.add_handler(RegexHandler(captions.CONTRIBUTING, help.contributing))
    dp.add_handler(RegexHandler(captions.EXAMPLES, help.examples))
    dp.add_handler(RegexHandler(captions.HELP, help.help))

    dp.add_handler(RegexHandler("^/edit\d+$", admin.edit_bot, pass_chat_data=True))
    dp.add_handler(CommandHandler('reject', admin.reject_bot_submission))
    dp.add_handler(CommandHandler('rej', admin.reject_bot_submission))

    dp.add_handler(CommandHandler('new', contributions.new_bot_submission, pass_args=True, pass_chat_data=True))
    dp.add_handler(RegexHandler('.*#new.*', contributions.new_bot_submission, pass_chat_data=True))
    dp.add_handler(CommandHandler('offline', contributions.notify_bot_offline, pass_args=True))
    dp.add_handler(RegexHandler('.*#offline.*', contributions.notify_bot_offline))
    dp.add_handler(CommandHandler('spam', contributions.notify_bot_spam, pass_args=True))
    dp.add_handler(RegexHandler('.*#spam.*', contributions.notify_bot_spam))
    dp.add_handler(RegexHandler('^{}$'.format(const.REGEX_BOT_ONLY), send_bot_details, pass_chat_data=True))

    dp.add_handler(CommandHandler('help', help.help))
    dp.add_handler(CommandHandler("contributing", help.contributing))
    dp.add_handler(CommandHandler("contribute", help.contributing))
    dp.add_handler(CommandHandler("examples", help.examples))
    dp.add_handler(CommandHandler("rules", help.rules))

    dp.add_handler(CommandHandler("addfavorite", favorites.send_favorites_list))
    dp.add_handler(CommandHandler("addfav", favorites.add_favorite_handler, pass_args=True))
    dp.add_handler(CommandHandler("f", favorites.send_favorites_list))
    dp.add_handler(CommandHandler("fav", favorites.send_favorites_list))
    dp.add_handler(CommandHandler("favorites", favorites.send_favorites_list))

    dp.add_handler(CommandHandler('ban', lambda bot, update, args: admin.ban_handler(
        bot, update, args, True), pass_args=True))
    dp.add_handler(CommandHandler('unban', lambda bot, update, args: admin.ban_handler(
        bot, update, args, False), pass_args=True))
    dp.add_handler(CommandHandler('t3chno', t3chnostats))
    dp.add_handler(CommandHandler('random', eastereggs.send_random_bot))
    dp.add_handler(CommandHandler('easteregg', eastereggs.send_next, pass_args=True))

    dp.add_handler(CommandHandler("subscribe", manage_subscription))
    dp.add_handler(CommandHandler("newbots", show_new_bots, pass_chat_data=True))

    dp.add_handler(CommandHandler("accesstoken", access_token))

    dp.add_handler(ChosenInlineResultHandler(inlinequery.chosen_result, pass_chat_data=True))
    dp.add_handler(InlineQueryHandler(inlinequery.inlinequery_handler, pass_chat_data=True))
    dp.add_handler(MessageHandler(Filters.all, all_handler, pass_chat_data=True), group=1)

    # TODO: put all handlers in their components' register()-methods
    basic.register(dp)

    # users = User.select().join(
    #     Bot.select(
    #         Bot.submitted_by, fn.COUNT(Bot.submitted_by).alias('num_submissions')
    #     ), on=(Bot.submitted_by == )
    # )
    # users = User.select().join(
    #     Bot.select(
    #         Bot.submitted_by, fn.COUNT(Bot.submitted_by).alias('num_submissions')
    #     ).group_by(Bot.submitted_by), on=Bot.submitted_by
    # )
    # pprint(users)


    # JOBS
    # updater.job_queue.put(Job(channel_checker_job, TIME), next_t=0)
    updater.job_queue.run_repeating(admin.last_update_job, interval=60 * 60 * 24)  # 60*60

    updater.start_polling()

    log.info('Listening...')
    updater.idle()
    log.info('Disconnecting...')
    appglobals.disconnect()


if __name__ == '__main__':
    main()
