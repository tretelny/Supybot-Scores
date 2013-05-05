# -*- coding: utf-8 -*-
###
# Copyright (c) 2012-2013, spline
# All rights reserved.
###

# my libs
from BeautifulSoup import BeautifulSoup, NavigableString
import re
import datetime
import sqlite3
import os.path
import base64
# supybot libs
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
from supybot.i18n import PluginInternationalization, internationalizeDocstring

_ = PluginInternationalization('Scores')


@internationalizeDocstring
class Scores(callbacks.Plugin):
    """Add the help for "@plugin help Scores" here
    This should describe *how* to use this plugin."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(Scores, self)
        self.__parent.__init__(irc)
        self.scoresdb = os.path.abspath(os.path.dirname(__file__)) + '/db/scores.db'

    ##############
    # FORMATTING #
    ##############

    def _red(self, string):
        """Returns a red string."""
        return ircutils.mircColor(string, 'red')

    def _yellow(self, string):
        """Returns a yellow string."""
        return ircutils.mircColor(string, 'yellow')

    def _green(self, string):
        """Returns a green string."""
        return ircutils.mircColor(string, 'green')

    def _bold(self, string):
        """Returns a bold string."""
        return ircutils.bold(string)

    def _ul(self, string):
        """Returns an underline string."""
        return ircutils.underline(string)

    def _bu(self, string):
        """Returns a bold/underline string."""
        return ircutils.bold(ircutils.underline(string))

    ##############
    # PROCESSING #
    ##############

    def _splicegen(self, maxchars, stringlist):
        """Return a group of splices from a list based on the maxchars
        string-length boundary.
        """

        runningcount = 0
        tmpslice = []
        for i, item in enumerate(stringlist):
            runningcount += len(item)
            if runningcount <= int(maxchars):
                tmpslice.append(i)
            else:
                yield tmpslice
                tmpslice = [i]
                runningcount = len(item)
        yield(tmpslice)

    def _validate(self, date, format):
        """Return true or false for valid date based on format."""

        try:
            datetime.datetime.strptime(str(date), format) # format = "%Y%m%D"
            return True
        except ValueError:
            return False

    def _stripcomma(self, string):
        """Return a string with everything after the first comma removed."""

        return string.split(',',1)[0]

    def _boldleader(self, atm, asc, htm, hsc):
        """Input away team, away score, home team, home score and bold the leader of the two."""

        if int(asc) > int(hsc):
            return("{0} {1} {2} {3}".format(self._bold(atm), self._bold(asc), htm, hsc))
        elif int(hsc) > int(asc):
            return("{0} {1} {2} {3}".format(atm, asc, self._bold(htm), self._bold(hsc)))
        else:
            return("{0} {1} {2} {3}".format(atm, asc, htm, hsc))

    def _colorformatstatus(self, string):
        """Handle the formatting of a status with color."""

        table = {# Red
                 'Final':self._red('F'),'F/OT':self._red('F/OT'),'F/2OT':self._red('F/2OT'),
                 'F/3OT':self._red('F/3OT'),'F/4OT':self._red('F/4OT'),'F/5OT':self._red('F/5OT'),
                 'Canc':self._red('CAN'),'F/SO':self._red('F/SO'),
                 # Green
                 '1st':self._green('1st'),'2nd':self._green('2nd'),'3rd':self._green('3rd'),
                 '4th':self._green('4th'),'OT':self._green('OT'),'SO':self._green('SO'),
                 # Yellow
                 'Half':self._yellow('H'),'Dly':self._yellow('DLY'),'DLY':self._yellow('DLY'),
                 'PPD':self._yellow('PPD'),'Del:':self._yellow('DLY'),'Int':self._yellow('INT'),
                 'Del':self._yellow('DLY')
                 }
        try:
            return table[string]
        except:
            return string

    def _mlbformatstatus(self, string):
        """Handle MLB specific status here."""

        # conditionals for each.
        if string.startswith('F'):  # final or F/10 for innings.
            string = string.replace('Final', 'F')  # Final to F.
            string = self._red(string)
        elif string.startswith('Top ') or string.startswith('Bot ') or string.startswith('End') or string.startswith('Mid'):  # Top or Bot.
            string = string.replace('Top ', 'T')  # Top to T.
            string = string.replace('Bot ', 'B')  # Bot to B.
            string = string.replace('End ', 'E')  # End to E.
            string = string.replace('Mid ', 'M')  # Mid to M.
            string = string.replace('th', '').replace('nd', '').replace('rd', '').replace('st', '')  # remove endings.
            string = self._green(string)
        elif string.startswith('Dly') or string.startswith('PPD') or string.startswith('Del') or string.startswith('Susp'):  # delayed
            if string == "PPD":  # PPD is one thing, otherwise..
                string = self._yellow('PPD')
            else:  # it can be "DLY: End 5th." or "Susp: Bot 9th". I don't want to do conditionals here.
                string = self._yellow('DLY')
        # return now.
        return string

    def _handlestatus(self, sport, string):
        """Handle working with the time/status of a game."""

        if sport != 'mlb':  # handle all but MLB here.
            strings = string.split(' ', 1)  # split at space, everything in a list w/two.
            if len(strings) == 2:  # if we have two items, like 3:00 4th.
                return "{0} {1}".format(strings[0], self._colorformatstatus(strings[1]))  # ignore time and colorize quarter/etc.
            else:  # game is "not in progress"
                return self._colorformatstatus(strings[0])  # just return the colorized quarter/etc due to no time.
        else:  # handle MLB here.
            string = self._mlbformatstatus(string)
            return string

    def _fetch(self, optargs):
        """HTML Fetch."""

        url = base64.b64decode('aHR0cDovL20uZXNwbi5nby5jb20v') + '%s&wjb=' % optargs
        try:
            page = utils.web.getUrl(url)
            return page
        except utils.web.Error as e:
            self.log.error("ERROR. Could not open {0} message: {1}".format(url, e))
            return None

    def _scores(self, html, sport="", fullteams=True, showlater=True):
        """Go through each "game" we receive and process the data."""
        soup = BeautifulSoup(html)
        # subdark = soup.find('div', attrs={'class': 'sub dark'})
        games = soup.findAll('div', attrs={'id': re.compile('^game.*?')})
        # setup the list for output.
        gameslist = []
        # go through each game
        for game in games:
            gametext = self._stripcomma(game.getText())  # remove cruft after comma.
            if " at " not in gametext:  # game is in-action.
                if sport == 'nfl' or sport == 'ncf':  # special for NFL/NCB to display POS.
                    if game.find('b', attrs={'class': 'red'}):
                        gametext = gametext.replace('*', '<RZ>')
                    else:
                        gametext = gametext.replace('*', '<>')
                # make sure we split into parts and shove whatever status/time is in the rest.
                gparts = gametext.split(" ", 4)
                if fullteams:  # gparts[0] = away/2=home. full translation table.
                    gparts[0] = self._transteam(gparts[0], optsport=sport)
                    gparts[2] = self._transteam(gparts[2], optsport=sport)
                # last exception: color <RZ> or * if we have them.
                if sport == 'nfl' or sport == 'ncb':  # cheap but works.
                    gparts[0] = gparts[0].replace('<RZ>', self._red('<RZ>')).replace('<>', self._red('<>'))
                    gparts[2] = gparts[2].replace('<RZ>', self._red('<RZ>')).replace('<>', self._red('<>'))
                # now bold the leader and format output.
                gamescore = self._boldleader(gparts[0], gparts[1], gparts[2], gparts[3])
                output = "{0} {1}".format(gamescore, self._handlestatus(sport, gparts[4]))
            else:  # TEAM at TEAM time for inactive games.
                if not showlater:  # don't show these if !
                    break
                gparts = gametext.split(" ", 3)  # remove AM/PM in split.
                if fullteams:  # full teams.
                    gparts[0] = self._transteam(gparts[0], optsport=sport)
                    gparts[2] = self._transteam(gparts[2], optsport=sport)
                if "AM" not in gparts[3] and "PM" not in gparts[3]:  # for PPD in something not started.
                    gparts[3] = self._colorformatstatus(gparts[3])
                output = "{0} at {1} {2}".format(gparts[0], gparts[2], gparts[3])

            gameslist.append(output)  # finally add whatever output is.

        return gameslist  # return the list of games.

    #####################
    # DATABASE FUNCTION #
    #####################

    def _transteam(self, optteam, optsport=""):
        # do some regex here to parse out the team.
        partsregex = re.compile(r'(?P<pre>\<RZ\>|\<\>)?(?P<team>[A-Z\-&;]+)(?P<rank>\(\d+\))?')
        m = partsregex.search(optteam)
        # replace optteam with the team if we have it
        if m.group('team'):
            optteam = m.group('team')
        # connect and do the db translation.
        conn = sqlite3.connect(self.scoresdb)
        cursor = conn.cursor()
        cursor.execute("select full from teams where short=? and sport=?", (optteam, optsport))
        row = cursor.fetchone()
        cursor.close()
        # put the string/team back together with or without db output.
        if row is None:
            team = optteam
        else:
            team = str(row[0])
        # now lets build for output.
        output = ""  # blank string to start.
        if m.group('pre'):   # readd * or <RZ>
            output += m.group('pre')
        output += team  # now team.
        if m.group('rank'):
            output += m.group('rank')
        # finally, return output.
        return output

    ###################################
    # PUBLIC FUNCTIONS (ONE PER SPORT #
    ###################################

    def nba(self, irc, msg, args, optlist, optinput):
        """[--date YYYYMMDD] [optional]
        Display NBA scores.
        Use --date YYYYMMDD to display scores on specific date. Ex: --date 20121225
        Specify a string to match after to only display specific scores. Ex: Knick
        """

        # first, declare sport.
        optsport = 'nba'
        # base url.
        url = '%s/scoreboard?' % optsport
        # declare variables we manip with input + optlist.
        showlater = True  # show all. ! below negates.
        # first, we have to handle if optinput is today or tomorrow.
        if optinput:
            optinput = optinput.lower()  # lower to process.
            if optinput == "today":
                url += 'date=%s' % datetime.date.today().strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "tomorrow":
                tomorrow = datetime.date.today() + datetime.timedelta(days=1)  # today+1
                url += 'date=%s' % tomorrow.strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "!":
                showlater = False  # only show completed and active (not future) games.
                optinput = None  # have to declare so we're not looking for games below.
        # handle optlist.
        if optlist:  # we only have --date, for now.
            for (key, value) in optlist:
                if key == 'date':  # this will override today and tomorrow.
                    if len(str(value)) != 8 or not self._validate(value, '%Y%m%d'):
                        irc.reply("ERROR: Invalid date. Must be YYYYmmdd. Ex: --date=20120904")
                        return
                    else:
                        url += 'date=%s' % value
        # process url and fetch.
        html = self._fetch(url)
        if html == 'None':
            irc.reply("ERROR: Cannot fetch {0} scores url. Try again in a minute.".format(optsport.upper()))
            return
        # process games.
        gameslist = self._scores(html, sport=optsport, fullteams=self.registryValue('fullteams', msg.args[0]), showlater=showlater)
        # strip color/bold/ansi if option is enabled.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]
        # output time.
        if len(gameslist) > 0:  # process here if we have games. sometimes we do not.
            if optinput:  # we're looking for a specific team/string.
                count = 0  # start at 0.
                for item in gameslist:  # iterate through our items.
                    if optinput in item.lower():  # we're lower from above. lower item to match.
                        if count < 10:  # if less than 10 items out.
                            irc.reply(item)  # output item.
                            count += 1  # ++count.
                        else:  # once we're over 10 items out.
                            irc.reply("ERROR: I found too many matches for '{0}' in {1}. Try something more specific.".format(optinput, optsport.upper()))
                            break
            else:  # no optinput so we are just displaying games.
                if self.registryValue('lineByLineScores', msg.args[0]):  # if you want line-by-line scores, even for all.
                    for game in gameslist:
                        irc.reply(game)  # output each.
                else:  # we're gonna display as much as we can on each line.
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # we found no games to display.
            irc.reply("ERROR: No {0} games listed.".format(optsport.upper()))

    nba = wrap(nba, [getopts({'date':('int')}), optional('text')])

    def nhl(self, irc, msg, args, optlist, optinput):
        """[--date YYYYMMDD] [optional]
        Display NHL scores.
        Use --date YYYYMMDD to display scores on specific date. Ex: --date 20121225
        Specify a string to match after to only display specific scores. Ex: Rang
        """

         # first, declare sport.
        optsport = 'nha'
        # base url.
        url = '%s/scoreboard?' % optsport
        # declare variables we manip with input + optlist.
        showlater = True  # show all. ! below negates.
        # first, we have to handle if optinput is today or tomorrow.
        if optinput:
            optinput = optinput.lower()  # lower to process.
            if optinput == "today":
                url += 'date=%s' % datetime.date.today().strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "tomorrow":
                tomorrow = datetime.date.today() + datetime.timedelta(days=1)  # today+1
                url += 'date=%s' % tomorrow.strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "!":
                showlater = False  # only show completed and active (not future) games.
                optinput = None  # have to declare so we're not looking for games below.
        # handle optlist.
        if optlist:  # we only have --date, for now.
            for (key, value) in optlist:
                if key == 'date':  # this will override today and tomorrow.
                    if len(str(value)) != 8 or not self._validate(value, '%Y%m%d'):
                        irc.reply("ERROR: Invalid date. Must be YYYYmmdd. Ex: --date=20120904")
                        return
                    else:
                        url += 'date=%s' % value
        # process url and fetch.
        html = self._fetch(url)
        if html == 'None':
            irc.reply("ERROR: Cannot fetch {0} scores url. Try again in a minute.".format(optsport.upper()))
            return
        # process games.
        gameslist = self._scores(html, sport=optsport, fullteams=self.registryValue('fullteams', msg.args[0]), showlater=showlater)
        # strip color/bold/ansi if option is enabled.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]
        # output time.
        if len(gameslist) > 0:  # process here if we have games. sometimes we do not.
            if optinput:  # we're looking for a specific team/string.
                count = 0  # start at 0.
                for item in gameslist:  # iterate through our items.
                    if optinput in item.lower():  # we're lower from above. lower item to match.
                        if count < 10:  # if less than 10 items out.
                            irc.reply(item)  # output item.
                            count += 1  # ++count.
                        else:  # once we're over 10 items out.
                            irc.reply("ERROR: I found too many matches for '{0}' in {1}. Try something more specific.".format(optinput, optsport.upper()))
                            break
            else:  # no optinput so we are just displaying games.
                if self.registryValue('lineByLineScores', msg.args[0]):  # if you want line-by-line scores, even for all.
                    for game in gameslist:
                        irc.reply(game)  # output each.
                else:  # we're gonna display as much as we can on each line.
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # we found no games to display.
            irc.reply("ERROR: No {0} games listed.".format(optsport.upper()))

    nhl = wrap(nhl, [getopts({'date':('int')}), optional('text')])

    def nfl(self, irc, msg, args, optinput):
        """[team]
        Display NFL scores.
        Specify a string to match after to only display specific scores. Ex: Pat
        """

        html = self._fetch('nfl/scoreboard?')
        if html == 'None':
            irc.reply("Cannot fetch NFL scores.")
            return

        if optinput and optinput == "!":
            showlater = False
            optinput = None
        else:
            showlater = True

        gameslist = self._scores(html, sport='nfl', fullteams=self.registryValue('fullteams', msg.args[0]), showlater=showlater)

        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]

        if len(gameslist) > 0:
            if optinput:
                count = 0
                for item in gameslist:
                    if optinput.lower() in item.lower():
                        if count < 10:
                            irc.reply(item)
                            count += 1
                        else:
                            irc.reply("I found too many matches for '{0}'. Try something more specific.".format(optinput))
                            break
            else:
                if self.registryValue('lineByLineScores', msg.args[0]):
                    for game in gameslist:
                        irc.reply(game)
                else:
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:
            irc.reply("No NFL games listed.")

    nfl = wrap(nfl, [optional('text')])

    def mlb(self, irc, msg, args, optlist, optinput):
        """[--date YYYYMMDD] [optional]
        Display MLB scores.
        Use --date YYYYMMDD to display scores on specific date. Ex: --date 20121225
        Specify a string to match after to only display specific scores. Ex: Yank
        """

        # first, declare sport.
        optsport = 'mlb'
        # base url.
        url = '%s/scoreboard?' % optsport
        # declare variables we manip with input + optlist.
        showlater = True  # show all. ! below negates.
        # first, we have to handle if optinput is today or tomorrow.
        if optinput:
            optinput = optinput.lower()  # lower to process.
            if optinput == "today":
                url += 'date=%s' % datetime.date.today().strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "tomorrow":
                tomorrow = datetime.date.today() + datetime.timedelta(days=1)  # today+1
                url += 'date=%s' % tomorrow.strftime('%Y%m%d')
                optinput = None  # have to declare so we're not looking for games below.
            elif optinput == "!":
                showlater = False  # only show completed and active (not future) games.
                optinput = None  # have to declare so we're not looking for games below.
        # handle optlist.
        if optlist:  # we only have --date, for now.
            for (key, value) in optlist:
                if key == 'date':  # this will override today and tomorrow.
                    if len(str(value)) != 8 or not self._validate(value, '%Y%m%d'):
                        irc.reply("ERROR: Invalid date. Must be YYYYmmdd. Ex: --date=20120904")
                        return
                    else:
                        url += 'date=%s' % value
        # process url and fetch.
        html = self._fetch(url)
        if html == 'None':
            irc.reply("ERROR: Cannot fetch {0} scores url. Try again in a minute.".format(optsport.upper()))
            return
        # process games.
        gameslist = self._scores(html, sport=optsport, fullteams=self.registryValue('fullteams', msg.args[0]), showlater=showlater)
        # strip color/bold/ansi if option is enabled.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]
        # output time.
        if len(gameslist) > 0:  # process here if we have games. sometimes we do not.
            if optinput:  # we're looking for a specific team/string.
                count = 0  # start at 0.
                for item in gameslist:  # iterate through our items.
                    if optinput in item.lower():  # we're lower from above. lower item to match.
                        if count < 10:  # if less than 10 items out.
                            irc.reply(item)  # output item.
                            count += 1  # ++count.
                        else:  # once we're over 10 items out.
                            irc.reply("ERROR: I found too many matches for '{0}' in {1}. Try something more specific.".format(optinput, optsport.upper()))
                            break
            else:  # no optinput so we are just displaying games.
                if self.registryValue('lineByLineScores', msg.args[0]):  # if you want line-by-line scores, even for all.
                    for game in gameslist:
                        irc.reply(game)  # output each.
                else:  # we're gonna display as much as we can on each line.
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # we found no games to display.
            irc.reply("ERROR: No {0} games listed.".format(optsport.upper()))

    mlb = wrap(mlb, [getopts({'date':('int')}), optional('text')])

    def ncb(self, irc, msg, args, optlist, optconf):
        """[--date YYYYMMDD] [tournament|conference|team]
        Display College Basketball scores.
        Optional: Use --date YYYYMMDD to display scores on specific date. Ex: --date 20121225
        Optional: input CONFERENCE or TEAM to search scores by conference or display an individual team's score. Ex: SEC or Bama.
        Optional: input tournament to display scores. Ex: ncaa, nit.
        """

        # basketball confs.
        validconfs = {'top25':'999', 'a10':'3', 'acc':'2', 'ameast':'1', 'big12':'8', 'bigeast':'4', 'bigsky':'5', 'bigsouth':'6', 'big10':'7',
                      'bigwest':'9', 'c-usa':'11', 'caa':'10', 'greatwest':'57', 'horizon':'45', 'independent':'43', 'ivy':'12', 'maac':'13',
                      'mac':'14', 'meac':'16', 'mvc':'18', 'mwc':'44', 'div-i':'50', 'nec':'19', 'non-div-i':'51', 'ovc':'20', 'pac12':'21',
                      'patriot':'22', 'sec':'23', 'southern':'24', 'southland':'25', 'summit':'49', 'sunbelt':'27', 'swac':'26', 'wac':'30',
                      'wcc':'29', 'ncaa':'100', 'nit':'50', 'cbi':'55', 'cit':'56' }

        # if we have a specific conf to display, get the id.
        optinput = None
        if optconf:
            optconf = optconf.lower()
            if optconf not in validconfs:
                optinput = optconf

        # get top25 if no conf is specified.
        if optconf and optinput :  # no optinput because we got a conf above.
            url = 'ncb/scoreboard?groupId=%s' % validconfs['div-i']
        elif optconf and not optinput:
            url = 'ncb/scoreboard?groupId=%s' % validconfs[optconf]
        else:
            url = 'ncb/scoreboard?groupId=%s' % validconfs['top25']

        # handle date
        if optlist:
            for (key, value) in optlist:
                if key == 'date':
                    if len(str(value)) !=8 or not self._validate(value, '%Y%m%d'):
                        irc.reply("Invalid date. Must be YYYYmmdd. Ex: 20120904")
                        return
                    else:
                        url += '&date=%s' % value

        html = self._fetch(url)
        if html == 'None':
            irc.reply("Cannot fetch NCB scores.")
            return

        # now, process html and put all into gameslist.
        gameslist = self._scores(html, sport='ncb', fullteams=self.registryValue('fullteams', msg.args[0]))

        # strip ANSI if needed.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]

        # finally, check if there is any games/output.
        if len(gameslist) > 0:  # if we have games
            if optinput:  # if we have input.
                count = 0
                for item in gameslist:
                    if optinput.lower() in item.lower():
                        if count < 10:
                            irc.reply(item)
                            count += 1
                        else:
                            irc.reply("I found too many matches for '{0}'. Try something more specific.".format(optinput))
                            break
            else:
                if self.registryValue('lineByLineScores', msg.args[0]):
                    for game in gameslist:
                        irc.reply(game)
                else:
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # no games
            irc.reply("No college basketball games listed.")

    ncb = wrap(ncb, [getopts({'date':('int')}), optional('text')])

    def cfb(self, irc, msg, args, optconf):
        """[conference|team]
        Display College Football scores.
        Optional: input with conference to display all scores from conf. Use team to only display specific team scores.
        Ex: SEC or Bama or BIG10 or Notre
        """

        # football confs.
        validconfs = {'top25':'999', 'acc':'1', 'bigeast':'10', 'bigsouth':'40', 'big10':'5', 'big12':'4',
                      'bigsky':'20', 'caa':'48', 'c-usa':'12', 'independent':'18','ivy':'22', 'mac':'15',
                      'meac':'24', 'mvc':'21', 'i-a':'80','i-aa':'81', 'pac12':'9', 'southern':'29', 'sec':'8',
                      'sunbelt':'37', 'wac':'16'}

        # if we have a specific conf to display, get the id.
        optinput = None
        if optconf:
            optconf = optconf.lower()
            if optconf not in validconfs:
                optinput = optconf

        # get top25 if no conf is specified.
        if optconf and optinput :  # no optinput because we got a conf above.
            url = 'ncf/scoreboard?groupId=%s' % validconfs['i-a']
        elif optconf and not optinput:
            url = 'ncf/scoreboard?groupId=%s' % validconfs[optconf]
        else:
            url = 'ncf/scoreboard?groupId=%s' % validconfs['top25']

        html = self._fetch(url)
        if html == 'None':
            irc.reply("Cannot fetch CFB scores.")
            return

        # now, process html and put all into gameslist.
        gameslist = self._scores(html, sport='ncf', fullteams=self.registryValue('fullteams', msg.args[0]))

        # strip ANSI if needed.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]

        # finally, check if there is any games/output.
        if len(gameslist) > 0: # if we have games
            if optinput: # if we have input.
                count = 0
                for item in gameslist:
                    if optinput.lower() in item.lower():
                        if count < 10:
                            irc.reply(item)
                            count += 1
                        else:
                            irc.reply("I found too many matches for '{0}'. Try something more specific.".format(optinput))
                            break
            else:
                if self.registryValue('lineByLineScores', msg.args[0]):
                    for game in gameslist:
                        irc.reply(game)
                else:
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # no games
            irc.reply("No college football games listed.")

    cfb = wrap(cfb, [optional('text')])

    def ncw(self, irc, msg, args, optlist, optconf):
        """[--date YYYYMMDD] [conference|team]
        Display Women's College Basketball scores.
        Optional: Use --date YYYYMMDD to display scores on specific date. Ex: --date 20121225
        Optional: input CONFERENCE or TEAM to search scores by conference or display an individual team's score. Ex: SEC or Bama.
        Optional: input tournament to display scores. Ex: ncaa, nit.
        """

        # basketball confs.
        validconfs = {'top25':'999', 'a10':'3', 'acc':'2', 'ameast':'1', 'big12':'8', 'bigeast':'4', 'bigsky':'5', 'bigsouth':'6', 'big10':'7',
                      'bigwest':'9', 'c-usa':'11', 'caa':'10', 'greatwest':'57', 'horizon':'45', 'independent':'43', 'ivy':'12', 'maac':'13',
                      'mac':'14', 'meac':'16', 'mvc':'18', 'mwc':'44', 'div-i':'50', 'nec':'19', 'non-div-i':'51', 'ovc':'20', 'pac12':'21',
                      'patriot':'22', 'sec':'23', 'southern':'24', 'southland':'25', 'summit':'49', 'sunbelt':'27', 'swac':'26', 'wac':'30',
                      'wcc':'29', 'ncaa':'100', 'nit':'50', 'cbi':'55' }

        # if we have a specific conf to display, get the id.
        optinput = None
        if optconf:
            optconf = optconf.lower()
            if optconf not in validconfs:
                optinput = optconf

        # get top25 if no conf is specified.
        if optconf and optinput :  # no optinput because we got a conf above.
            url = 'ncw/scoreboard?groupId=%s' % validconfs['div-i']
        elif optconf and not optinput:
            url = 'ncw/scoreboard?groupId=%s' % validconfs[optconf]
        else:
            url = 'ncw/scoreboard?groupId=%s' % validconfs['top25']

        # handle date
        if optlist:
            for (key, value) in optlist:
                if key == 'date':
                    if len(str(value)) !=8 or not self._validate(value, '%Y%m%d'):
                        irc.reply("Invalid date. Must be YYYYmmdd. Ex: 20120904")
                        return
                    else:
                        url += '&date=%s' % value

        html = self._fetch(url)
        if html == 'None':
            irc.reply("Cannot fetch women's college basketball scores.")
            return

        # now, process html and put all into gameslist.
        gameslist = self._scores(html, sport='ncb', fullteams=self.registryValue('fullteams', msg.args[0]))

        # strip ANSI if needed.
        if self.registryValue('disableANSI', msg.args[0]):
            gameslist = [ircutils.stripFormatting(item) for item in gameslist]

        # finally, check if there is any games/output.
        if len(gameslist) > 0:  # if we have games
            if optinput:  # if we have input.
                count = 0
                for item in gameslist:
                    if optinput.lower() in item.lower():
                        if count < 10:
                            irc.reply(item)
                            count += 1
                        else:
                            irc.reply("I found too many matches for '{0}'. Try something more specific.".format(optinput))
                            break
            else:
                if self.registryValue('lineByLineScores', msg.args[0]):
                    for game in gameslist:
                        irc.reply(game)
                else:
                    for splice in self._splicegen('380', gameslist):
                        irc.reply(" | ".join([gameslist[item] for item in splice]))
        else:  # no games
            irc.reply("No women's college basketball games listed.")

    ncw = wrap(ncw, [getopts({'date':('int')}), optional('text')])

    def tennis(self, irc, msg, args, optmatch):
        """[mens|womens|mensdoubles|womensdoubles|mixeddoubles]
        Display current Tennis scores. Defaults to Men's Singles.
        Call with argument to display others. Ex: womens
        """

        if optmatch:
            optmatch = optmatch.lower()
            if optmatch == "womens":
                matchType = "2"
            elif optmatch == "mensdoubles":
                matchType = "3"
            elif optmatch == "womensdoubles":
                matchType = "4"
            elif optmatch == "mixeddoubles":
                matchType = "6"
            else:
                matchType = "1"
        else:
            matchType = "1"

        html = self._fetch('general/tennis/dailyresults?matchType=%s' % matchType)
        if html == 'None':
            irc.reply("Cannot fetch Tennis scores.")
            return

        # one easy sanity check.
        if "There are no matches scheduled." in html:
            irc.reply("ERROR: There are no matches scheduled for: %s" % optmatch)
            return

        # process html.
        soup = BeautifulSoup(html,convertEntities=BeautifulSoup.HTML_ENTITIES)
        matches = soup.findAll('div', attrs={'class':re.compile('^ind|^ind alt')})
        if len(matches) < 1:  # second sanity check.
            irc.reply("ERROR: No %s tennis matches found" % optmatch)
            return
        title = soup.find('div', attrs={'class':'sec row'})
        tennisRound = soup.findAll('div', attrs={'class':'ind sub bold'})[1]

        # now iterate through each match and populate output.
        output = []
        for each in matches:
            if each.find('b'):  # <b> means it is a tennis match.
                status = each.find('b').extract()
                if status.text == "Final":
                    status = self._red("F")
                else:
                    status = self._bold(status.text)
                matchText = []
                for item in each.contents:
                    if isinstance(item, NavigableString):
                        matchText.append(item.strip())
                output.append("{0} :: {1}".format(status, " ".join(matchText)))

        # now output.
        if len(output) < 1:
            irc.reply("Error: no matches to output.")
        elif len(output) < 6:
            irc.reply("{0} {1}".format(self._bold(title.getText()),self._bold(tennisRound.getText())))
            for each in output:
                irc.reply("{0}".format(each))
        else:
            irc.reply("{0} {1}".format(self._bold(title.getText()),self._bold(tennisRound.getText())))
            irc.reply("{0}".format(" | ".join(output)))

    tennis = wrap(tennis, [optional('somethingWithoutSpaces')])

    def golf(self, irc, msg, args, optseries, optinput):
        """[pga|web.com|champions|lpga|euro]
        Display current Golf scores from a PGA tournament. Specify a specific series to show different scores.
        Ex: lpga
        """

        if optseries:
            optseries = optseries.lower()
            if optseries == "pga":
                seriesId = "1"
            if optseries == "web.com":
                seriesId = "2"
            elif optseries == "champions":
                seriesId = "3"
            elif optseries == "lpga":
                seriesId = "4"
            elif optseries == "euro":
                seriesId = "5"
            else:
                seriesId = "1"
        else:  # go pga if we don't have a series.
            seriesId = "1"

        html = self._fetch('golf/eventresult?seriesId=%s' % seriesId)
        if html == 'None':
            irc.reply("ERROR: Cannot fetch Golf scores.")
            return

        soup = BeautifulSoup(html)
        golfEvent = soup.find('div', attrs={'class': 'sub dark big'})
        golfStatus = soup.find('div', attrs={'class': 'sec row', 'style': 'white-space: nowrap;'})
        # process the event/title and status.
        if str(golfEvent.getText()).startswith("Ryder Cup"):  # special status for Ryder Cup.
            rows = soup.find('div', attrs={'class':'ind'})

            irc.reply(self._green(golfEvent.getText()))
            irc.reply("{0}".format(rows.getText()))
            return
        else:  # regular tournaments.
            table = soup.find('table', attrs={'class': 'wide'})
            if not table:
                irc.reply("ERROR: Could not find golf results. Tournament not going on?")
                return
            rows = table.findAll('tr')[1:]  # skip header row.

        append_list = []
        # process rows. each row is a player.
        for row in rows:
            tds = row.findAll('td')
            pRank = tds[0].getText()
            pPlayer = tds[1].getText()
            pScore = tds[2].getText()
            pRound = tds[3].getText()
            pRound = pRound.replace('(', '').replace(')', '')  # remove ( ). We process pRound later.
            # handle strings differently, depending on if started or not. not started also includes cut.
            if "am" in pRound or "pm" in pRound or pScore == "CUT":  # append string conditional if they started or not.
                if pScore == "CUT":  # we won't have a pRound score in this case
                    appendString = "{0}. {1} {2}".format(pRank, self._bold(pPlayer), pScore)
                else:
                    appendString = "{0}. {1} {2} ({3})".format(pRank, self._bold(pPlayer), pScore, pRound)
            else:  # player has started.
                pRound = pRound.split(' ', 1)  # we split -2 (F), but might not be two.
                if len(pRound) == 2:  # normally, it looks like -2 (F). We want the F.
                    pRound = pRound[1]
                else:  # It is just -9 like for a playoff.
                    pRound = pRound[0]
                appendString = "{0}. {1} {2} ({3})".format(pRank, self._bold(pPlayer), pScore, pRound)
            append_list.append(appendString)

        # output time.
        if golfEvent != None and golfStatus != None:  # header/tournament
            irc.reply("{0} - {1}".format(self._green(golfEvent.getText()), self._bold(golfStatus.getText())))
        if not optinput:  # just show the leaderboard
            irc.reply(" | ".join([item for item in append_list]))
        else:  # display a specific player's score.
            count = 0  # for max 5.
            for each in append_list:
                if optinput.lower() in each.lower():  # if we find a match.
                    if count < 5:  # only show five.
                        irc.reply(each)
                        count += 1
                    else:  # above this, display the error.
                        irc.reply("I found too many results for '{0}'. Please specify something more specific".format(optinput))
                        break

    golf = wrap(golf, [optional('somethingWithoutSpaces'), optional('text')])

    def nascar(self, irc, msg, args, optrace):
        """[sprintcup|nationwide]
        Display active NASCAR standings in race.
        Defaults to Sprint Cup.
        """

        if optrace:
            optrace = optrace.lower()
            if optrace == "sprintcup":
                raceType = "2"
            elif optrace == "nationwide":
                raceType = "3"
            else:
                raceType = "2"
        else:
            raceType = "2"

        html = self._fetch('rpm/nascar/eventresult?seriesId=%s' % raceType)
        if html == 'None':
            irc.reply("Cannot fetch NASCAR stats.")
            return

        soup = BeautifulSoup(html)
        race = soup.find('div', attrs={'class': 'sub dark big'}).getText().replace(' Results', '').strip()
        racestatus = soup.find('div', attrs={'class': 'sec row'}).getText().strip()

        standings = []

        rtable = soup.find('table', attrs={'class': 'wide', 'cellspacing': '0', 'width': '100%'})
        rows = rtable.findAll('tr')[1:]
        for row in rows:
            tds = row.findAll('td')
            place = tds[0].getText().strip()
            driver = tds[1].getText().strip()
            behind = tds[2].getText().strip()
            standings.append("{0}. {1} - {2}".format(place, self._bold(driver), behind))

        irc.reply("{0} :: {1}".format(self._red(race), self._ul(racestatus)))
        irc.reply("{0}".format(" | ".join(standings)))

    nascar = wrap(nascar, [optional('somethingWithoutSpaces')])


Class = Scores

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=250:
