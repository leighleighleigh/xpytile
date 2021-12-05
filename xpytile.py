#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
X-tiling helper
with simultaneous resizing of docked (side-by-side) windows


Copyright (C) 2021  jaywilkas  <just4 [period] gmail [at] web [period] de>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import configparser
import datetime
from functools import lru_cache
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import Xlib.display, Xlib.XK, Xlib.error, Xlib.protocol


# ----------------------------------------------------------------------------------------------------------------------
class _list(object):
    """
    Very simple "auto-expanding list like" - class
    Purpose: Easily handle desktop-specific tilingInfo when there are more desktops
             than the number of configured desktop-specfic entries (for example
             when the number of desktops get increased at runtime).
    """

    def __init__(self, args=[]):
        self._list = args
        self.default_value = None

    def expand(self, i):
        if self.default_value is None and len(self._list):
            self.default_value = self._list[0]
        for n in range(len(self._list), i + 1):
            self._list.append(self.default_value)

    def __getitem__(self, i):
        if i >= len(self._list):
            self.expand(i)
        return self._list[i]

    def __setitem__(self, i, val):
        if i >= len(self._list):
            self.expand(i - 1)
        self._list[i] = val

    def append(self, val):
        self._list.append(val)

    def set_default(self, val):
        self.default_value = val

    def __str__(self):
        return str(self._list)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def change_num_max_windows_by(deltaNum):
    """
    Change the max number of windows to tile  (limited between minimal 2 and maximal 9 windows)
    :param   deltaNum: increment number of max windows by this value
    :return:
    """
    global Xroot, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    tilerNumber = tilingInfo['tiler'][currentDesktop]
    tilerNames_dict = {1: 'masterAndStackVertic', 2: 'vertically', 3: 'masterAndStackHoriz', 4: 'horizontally'}
    try:
        tilerName = tilerNames_dict[tilerNumber]
    except KeyError:
        return

    tilingInfo[tilerName]['maxNumWindows'] = min(max(tilingInfo[tilerName]['maxNumWindows'] + deltaNum, 2), 9)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def cycle_windows():
    """
    Cycles all -not minimized- windows of the current desktop
    :return:
    """
    global disp, Xroot, NET_CURRENT_DESKTOP

    # get a list of all -not minimized and not ignored- windows of the current desktop
    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    winIDs = get_windows_on_desktop(currentDesktop)

    if len(winIDs) < 2:
        return

    for i, winID in enumerate(winIDs):
        try:
            winID_next = winIDs[i + 1]
        except IndexError as e:
            winID_next = winIDs[0]
        set_window_position(winID, x=windowsInfo[winID_next]['x'], y=windowsInfo[winID_next]['y'])
        set_window_size(winID, width=windowsInfo[winID_next]['width'], height=windowsInfo[winID_next]['height'])

    disp.sync()
    update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def get_moved_border(winID, window):
    """
    Return which border(s) of the window have been moved

    :param winID:    ID of the window
    :param window:   window
    :return:         a number that indicates which window edges got shifted
    """
    global windowsInfo

    moved_border = 0
    try:
        winInfo = windowsInfo[winID]
        geometry = get_window_geometry(window)
        if geometry is None:  # window vanished
            return moved_border
    except KeyError:
        return moved_border

    if winInfo['x'] != geometry.x:
        moved_border += 1  # left border
    if winInfo['y'] != geometry.y:
        moved_border += 2  # upper border
    if winInfo['x2'] != geometry.x + geometry.width - 1:
        moved_border += 4  # right border
    if winInfo['y2'] != geometry.y + geometry.height - 1:
        moved_border += 8  # lower border

    return moved_border
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def get_parent_window(window):
    """
    Thanks to this post:  stackoverflow.com/questions/60141048/
    Because an X window is not necessarily just what one thinks of
    as a window (the window manager may add an invisible frame, and
    so on), we record not just the active window but its ancestors
    up to the root, and treat a ConfigureNotify on any of those
    ancestors as meaning that the active window has been moved or resized.

    :param window:    window
    :return:          parent window
    """

    try:
        pointer = window
        while pointer.id != Xroot.id:
            parentWindow = pointer
            pointer = pointer.query_tree().parent

        return parentWindow
    except:
        return None  # window vanished
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def get_window_geometry(win):
    """
    Return the geometry of the top most parent window.
    See the comment in get_active_window_and_ancestors()

    :param win:    window
    :return:       geometry of the top most parent window
    """

    try:
        return get_parent_window(win).get_geometry()
    except:
        return None   # window vanished
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def get_windows_name(winID, window):
    """
    Get the application name of the window.
    Tries at first to find the name in the windowsInfo structure.
    If the winID is not yet known, get_wm_class() gets called.

    :param winID:    ID of the window
    :param window:   window
    :return:         name of the window / application
    """
    global windowsInfo

    try:
        name = windowsInfo[winID]['name']
    except KeyError:
        try:
            wmclass, name = window.get_wm_class()
        except TypeError:
            name = "UNKNOWN"

    return name
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def get_windows_on_desktop(desktop):
    """
    Return a list of window-IDs of all -not minimized and not sticky-
    windows from our list on the given desktop

    :param  desktop:   number of desktop
    :return:           list of window-IDs
    """
    global windowsInfo, NET_WM_STATE_HIDDEN, NET_WM_STATE_STICKY

    winIDs = list()
    for winID, winInfo in windowsInfo.items():
        try:
            if winInfo['desktop'] == desktop:
                propertyList = windowsInfo[winID]['win'].get_full_property(NET_WM_STATE, 0).value.tolist()
                if NET_WM_STATE_STICKY not in propertyList and NET_WM_STATE_HIDDEN not in propertyList:
                    winIDs.append(winID)
        except Xlib.error.BadWindow:
            pass  # window vanished

    return winIDs
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
@lru_cache
def get_windows_title(window):
    """
    Get the title of the window.

    :param window:   window
    :return:         title of the window / application
    """
    global NET_WM_NAME

    try:
        title = window.get_full_property(NET_WM_NAME, 0).value
        if isinstance(title, bytes):
            title = title.decode('UTF8', 'replace')
    except:
        title = '<unnamed?>'

    return title
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def handle_key_event(keyCode, windowID_active, window_active):
    """
    Perform the action associated with the hotkey

    :param keyCode:          The code of the pressed (to be precise: released) hotkey
    :param windowID_active:  ID of active window
    :param window_active:    active window
    :return:                 windowID_active, window_active
    """
    global hotkeys, tilingInfo, windowsInfo, disp

    if keyCode == hotkeys['toggleresize']:
        toggle_resize()
    elif keyCode == hotkeys['toggletiling']:
        if toggle_tiling():
            update_windows_info()
            tile_windows()
    elif keyCode == hotkeys['toggleresizeandtiling']:
        toggle_resize()
        if toggle_tiling():
            update_windows_info()
            tile_windows()
    elif keyCode == hotkeys['toggledecoration']:
        toggle_window_decoration()
    elif keyCode == hotkeys['enlargemaster']:
        tile_windows(resizeMaster=tilingInfo['stepSize'])
    elif keyCode == hotkeys['shrinkmaster']:
        tile_windows(resizeMaster=-tilingInfo['stepSize'])
    elif keyCode == hotkeys['togglemaximizewhenonewindowleft']:
        toggle_maximize_when_one_window()
    elif keyCode == hotkeys['cyclewindows']:
        update_windows_info()
        cycle_windows()
    elif keyCode == hotkeys['cycletiler']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber='next')
    elif keyCode == hotkeys['swapwindows']:
        update_windows_info()
        swap_windows(windowID_active)
    elif keyCode == hotkeys['tilemasterandstackvertically']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber=1)
    elif keyCode == hotkeys['tilevertically']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber=2)
    elif keyCode == hotkeys['tilemasterandstackhorizontally']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber=3)
    elif keyCode == hotkeys['tilehorizontally']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber=4)
    elif keyCode == hotkeys['tilemaximize']:
        update_windows_info()
        tile_windows(manuallyTriggered=True, tilerNumber=5)
    elif keyCode == hotkeys['increasemaxnumwindows']:
        change_num_max_windows_by(1)
        update_windows_info()
        tile_windows()
    elif keyCode == hotkeys['decreasemaxnumwindows']:
        change_num_max_windows_by(-1)
        update_windows_info()
        tile_windows()
    elif keyCode == hotkeys['recreatewindowslayout']:
        recreate_window_geometries()
    elif keyCode == hotkeys['storecurrentwindowslayout']:
        update_windows_info()
        store_window_geometries()
    elif keyCode == hotkeys['logactivewindow']:
        log_active_window(windowID_active, window_active)
    elif keyCode == hotkeys['focusup']:
        windowID_active, window_active = set_window_focus(windowID_active, window_active, 'up')
    elif keyCode == hotkeys['focusdown']:
        windowID_active, window_active = set_window_focus(windowID_active, window_active, 'down')
    elif keyCode == hotkeys['focusleft']:
        windowID_active, window_active = set_window_focus(windowID_active, window_active, 'left')
    elif keyCode == hotkeys['focusright']:
        windowID_active, window_active = set_window_focus(windowID_active, window_active, 'right')
    elif keyCode == hotkeys['exit']:
        # On exit, make sure all windows are decorated
        update_windows_info()
        for winID in windowsInfo:
            set_window_decoration(winID, True)
        disp.sync()
        notify('exit')
        quit()

    return windowID_active, window_active
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def init(configFile='~/.config/xpytilerc'):
    """
    Initialization
    configFile:  file-path of the config-file
    :return:     window_active, window_active_parent, windowID_active
    """
    global disp, Xroot, screen
    global windowsInfo
    global NET_ACTIVE_WINDOW, NET_WM_DESKTOP, NET_CLIENT_LIST, NET_CURRENT_DESKTOP, NET_WM_STATE_MAXIMIZED_VERT
    global NET_WM_STATE_MAXIMIZED_HORZ, NET_WM_STATE, NET_WM_STATE_HIDDEN, NET_WORKAREA, NET_WM_NAME, NET_WM_STATE_MODAL
    global NET_WM_STATE_STICKY, MOTIF_WM_HINTS, ANY_PROPERTYTYPE

    disp = Xlib.display.Display()
    screen = disp.screen()
    Xroot = screen.root

    NET_ACTIVE_WINDOW = disp.get_atom('_NET_ACTIVE_WINDOW')
    NET_WM_DESKTOP = disp.get_atom('_NET_WM_DESKTOP')
    NET_CLIENT_LIST = disp.get_atom('_NET_CLIENT_LIST')
    NET_CURRENT_DESKTOP = disp.get_atom('_NET_CURRENT_DESKTOP')
    NET_WM_STATE_MAXIMIZED_VERT = disp.get_atom('_NET_WM_STATE_MAXIMIZED_VERT')
    NET_WM_STATE_MAXIMIZED_HORZ = disp.get_atom('_NET_WM_STATE_MAXIMIZED_HORZ')
    NET_WM_STATE = disp.get_atom('_NET_WM_STATE')
    NET_WM_STATE_HIDDEN = disp.get_atom('_NET_WM_STATE_HIDDEN')
    NET_WM_NAME = disp.get_atom('_NET_WM_NAME')
    NET_WORKAREA = disp.get_atom('_NET_WORKAREA')
    NET_WM_STATE_MODAL = disp.get_atom('_NET_WM_STATE_MODAL')
    NET_WM_STATE_STICKY = disp.get_atom('_NET_WM_STATE_STICKY')
    MOTIF_WM_HINTS = disp.get_atom('_MOTIF_WM_HINTS')
    ANY_PROPERTYTYPE = Xlib.X.AnyPropertyType

    config = configparser.ConfigParser()
    config.read(os.path.expanduser(configFile))
    init_tiling_info(config)
    init_hotkeys_info(config)
    init_notification_info(config)

    # dictionary to keep track of the windows, their geometry and other information
    windowsInfo = dict()
    update_windows_info()

    # determine active window and its parent
    window_active = disp.get_input_focus().focus
    windowID_active = Xroot.get_full_property(NET_ACTIVE_WINDOW, ANY_PROPERTYTYPE).value[0]
    window_active_parent = get_parent_window(window_active)

    # configure event-mask
    Xroot.change_attributes(event_mask=Xlib.X.PropertyChangeMask | Xlib.X.SubstructureNotifyMask |
                                       Xlib.X.KeyReleaseMask)
    notify('start')

    return window_active, window_active_parent, windowID_active
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def init_hotkeys_info(config):
    """
    Read hotkey-config, fill hotkeys-dictionary and register key-combinations

    :param   config:  parsed config-file
    :return:
    """
    global hotkeys, Xroot

    modifier = config['Hotkeys'].getint('modifier')
    if modifier == -1:
        modifier = Xlib.X.AnyModifier

    hotkeys = dict()
    for item in config.items('Hotkeys'):
        if item[0] != 'modifier':
            hotkeys[item[0]] = int(item[1])
            Xroot.grab_key(int(item[1]), modifier, 1, Xlib.X.GrabModeAsync, Xlib.X.GrabModeAsync)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def init_notification_info(config):
    """
    Create a dict with notification configuration

    :param config:  parsed config-file
    :return:
    """
    global notificationInfo

    notificationInfo = dict()
    for item in config.items('Notification'):
        notificationInfo[item[0]] = item[1]
    notificationInfo['active'] = notificationInfo['active'] != 'False'
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def init_tiling_info(config):
    """
    Initialize the tiling info data structure
    :param   config:  parsed config-file
    :return:
    """
    global tilingInfo

    # ----------------------------------------------------------------------------
    def getConfigValue(config, sectionName, entryName, fallBackValue, type='int'):
        try:
            if type == 'int':
                value = config[sectionName].getint(entryName, fallback=fallBackValue)
            elif type == 'float':
                value = config[sectionName].getfloat(entryName, fallback=fallBackValue)
            elif type == 'bool':
                value = config[sectionName].getboolean(entryName, fallback=fallBackValue)
        except ValueError:
            value = fallBackValue
        return value

    # ----------------------------------------------------------------------------
    def parseConfigIgnoreWindowEntry(entry):

        retVal = {'name': None, 'title': None, '!title': None}
        strPos_name = strPos_title = None

        r = re.search('name: *".*"', entry)
        if r: strPos_name = r.span()[0]

        r = re.search('!{0,1}title: *".*"', entry)
        if r: strPos_title = r.span()[0]

        if strPos_name is None and strPos_title is None:
            return None

        if strPos_name is not None and (strPos_title is None or strPos_title > strPos_name):
            if strPos_title is not None:
                r = re.match('(name:\s*)(".*")(\s*)(!{0,1}title:\s*)(".*")', entry)
            else:
                r = re.match('(name:\s*)(".*")', entry)
            if r:
                retVal['name'] = re.compile(r.group(2)[1:-1])
                if strPos_title is not None:
                    retVal['title'] = re.compile(r.group(5)[1:-1])
                    retVal['!title'] = not r.group(4).startswith('!')

        return retVal

    # ----------------------------------------------------------------------------

    tilingInfo = dict()

    # configured settings that define ...
    #   ... what windows should be ignored depending on their name and title.
    tilingInfo['ignoreWindows'] = list()
    for line in config['General']['ignoreWindows'].split('\n'):
        entry = parseConfigIgnoreWindowEntry(line)
        if entry is not None:
            tilingInfo['ignoreWindows'].append(entry)

    #   ... which application should be tiled after some delay, depending on their name.
    tilingInfo['delayTilingWindowsWithNames'] = list()
    for entry in config['General']['delayTilingWindowsWithNames'].split('\n'):
        tilingInfo['delayTilingWindowsWithNames'].append(re.compile(entry[1:-1]))

    tilingInfo['delayTimeTiling'] = getConfigValue(config, 'General', 'delayTimeTiling', 0.5, 'float')

    #   ... resize- , tiling- and window-decoration - status for each desktop.
    tilingInfo['resizeWindows'] = _list([])
    tilingInfo['resizeWindows'].set_default(True)

    tilingInfo['tileWindows'] = _list([])
    tilingInfo['tileWindows'].set_default(True)

    tilingInfo['windowDecoration'] = _list([])
    tilingInfo['windowDecoration'].set_default(True)

    tilingInfo['tiler'] = _list([])
    tilingInfo['tiler'].set_default(getConfigValue(config, 'General', 'defaultTiler', 1))
    i = 1
    while True:
        _temp = getConfigValue(config, 'DefaultTilerPerDesktop', f'Desktop{i}', None)
        if _temp is None:
            break
        tilingInfo['tiler'].append(_temp)
        i += 1

    tilingInfo['maximizeWhenOneWindowLeft'] = _list([])
    _temp = getConfigValue(config, 'General', 'defaultMaximizeWhenOneWindowLeft', True, 'bool')
    tilingInfo['maximizeWhenOneWindowLeft'].set_default(_temp)
    i = 1
    while True:
        _temp = getConfigValue(config, 'maximizeWhenOneWindowLeft', f'Desktop{i}', None, 'bool')
        if _temp is None:
            break
        tilingInfo['maximizeWhenOneWindowLeft'].append(_temp)
        i += 1

    #   ... a margin, where edges with a distance smaller than that margin are considered docked.
    tilingInfo['margin'] = getConfigValue(config, 'General', 'margin', 100)

    #   ... a minimal size, so not to shrink width or height of a window smaller than this.
    tilingInfo['minSize'] = getConfigValue(config, 'General', 'minSize', 350)

    #   ... the increment when resizing the master window by hotkey.
    tilingInfo['stepSize'] = getConfigValue(config, 'General', 'stepSize', 50)

    tilingInfo['masterAndStackVertic'] = dict()
    tilingInfo['masterAndStackVertic']['maxNumWindows'] = \
        getConfigValue(config, 'masterAndStackVertic', 'maxNumWindows', 3)
    tilingInfo['masterAndStackVertic']['defaultWidthMaster'] = \
        getConfigValue(config, 'masterAndStackVertic', 'defaultWidthMaster', 0.5, 'float')

    tilingInfo['horizontally'] = dict()
    tilingInfo['horizontally']['maxNumWindows'] = \
        getConfigValue(config, 'horizontally', 'maxNumWindows', 3)

    tilingInfo['vertically'] = dict()
    tilingInfo['vertically']['maxNumWindows'] = \
        getConfigValue(config, 'vertically', 'maxNumWindows', 3)

    tilingInfo['masterAndStackHoriz'] = dict()
    tilingInfo['masterAndStackHoriz']['maxNumWindows'] = \
        getConfigValue(config, 'masterAndStackHoriz', 'maxNumWindows', 3)
    tilingInfo['masterAndStackHoriz']['defaultHeightMaster'] = \
        getConfigValue(config, 'masterAndStackHoriz', 'defaultHeightMaster', 0.5, 'float')

    tilingInfo['userDefinedGeom'] = dict()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def log_active_window(windowID_active, window_active):
    """
    Prints the name and the title of the currently active window into a log-file.
    The purpose of this function is to easily get the name and title of windows/applications
    which should be ignored.

    :param windowID_active:  ID of active window
    :param window_active:    active window
    :return:
    """

    fileName = os.path.join('/tmp', f'xpyfile_{os.environ["USER"]}.log')
    with open(fileName, 'a') as f:
        dateStr = datetime.datetime.strftime(datetime.datetime.now(), '%x %X')
        f.write(f'[{dateStr}]  name: {get_windows_name(windowID_active, window_active)},'
                f'  title: {get_windows_title(window_active)}\n')
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def match(compRexExList, string):
    """
    Check whether the string matches any of the regexes

    :param compRexExList:  list of compiled regex-pattern
    :param string:         string to test
    :return:
    """

    for r in compRexExList:
        if r.match(string):
            return True
    return False
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def match_ignore(ignoreWindows, name, title):
    """
    Checks whether to ignore the window, depending on its name and title

    :param ignoreWindows:      list of dict, what combinations of name/title should be ignored
    :param name:               name of the window/application
    :param title:              title of the window
    :return:                   status whether to ignore the window [True | False]
    """

    for e in ignoreWindows:
        if e['name'].match(name):
            if e['title'] is None:
                if verbosityLevel > 1:
                    print('Ignoring window:\t'
                          f'name "{name}" matches pattern "{e["name"].pattern}"\t'
                          f'title is irrelevant')
                return True
            if bool(e['title'].match(title)) == e['!title']:
                if verbosityLevel > 1:
                    print('Ignoring window:\t'
                          f'name "{name}" matches pattern "{e["name"].pattern}"\t'
                          f'{"!" * (not e["!title"])}title "{title}" {("does not match", "matches")[e["!title"]]}'
                          f'pattern "{e["title"].pattern}"')
                return True

    return False
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def notify(case, status=None):
    """
    Show a notification message (if active)

    :param case:     The circumstance
    :param status:   True / False (or None)
    :return:
    """
    global notificationInfo

    if not notificationInfo['active']:
        return

    case = case.lower()
    if status is not None:
        message = [notificationInfo['off_message'], notificationInfo['on_message']][int(status)]
        status_str = ['off', 'on'][int(status)]
    else:
        message = notificationInfo[f'{case}_message']
        status_str = ''

    iconFilePath = notificationInfo[f'{case}{status_str}_icon']
    summary = notificationInfo[f'{case}_summary']

    try:
        subprocess.Popen(['notify-send', '-t', notificationInfo['time'],
                          f'--icon={iconFilePath}', summary, message])
    except FileNotFoundError as e:
        pass
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def recreate_window_geometries():
    """
    Re-creates the geometry of all -not minimized- windows of the current desktop
    :return:
    """
    global tilingInfo, disp, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    # get a list of all -not minimized and not ignored- windows on the given desktop
    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    winIDs = get_windows_on_desktop(currentDesktop)

    for winID in winIDs:
        try:
            x = tilingInfo['userDefinedGeom'][currentDesktop][winID]['x']
            y = tilingInfo['userDefinedGeom'][currentDesktop][winID]['y']
            width = tilingInfo['userDefinedGeom'][currentDesktop][winID]['width']
            height = tilingInfo['userDefinedGeom'][currentDesktop][winID]['height']
            unmaximize_window(windowsInfo[winID]['win'])

            windowsInfo[winID]['win'].set_input_focus(Xlib.X.RevertToParent, Xlib.X.CurrentTime)
            windowsInfo[winID]['win'].configure(stack_mode=Xlib.X.Above)
            set_window_position(winID, x=x, y=y)
            set_window_size(winID, width=width, height=height)
            disp.sync()
            update_windows_info()
        except KeyError:
            pass  # window is not present anymore (on this desktop)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def resize_docked_windows(windowID_active, window_active, moved_border):
    """
    Resize the side-by-side docked windwows.
    The function deliberately retrieves the current window geometry of the active window
    rather than using the already existing information of the event structure.
    This saves a good amount of redrawing.

    :param windowID_active:   ID of the active window
    :param window_active:     active window
    :param moved_border:      points out which border of the active window got moved
    :return:
    """
    global disp, tilingInfo, NET_WORKAREA
    global windowsInfo  # dict with windows and their geometries (before last resize of the active window)

    tolerance = 3

    if moved_border not in [1, 2, 4, 8]:  # 1: left, 2: upper, 4: right, 8: lower
        return None

    winInfo_active = windowsInfo[windowID_active]

    # check whether resizing is active for the desktop of the resized window
    desktop = winInfo_active['desktop']
    if not tilingInfo['resizeWindows'][desktop]:
        return

    # geometry of work area (screen without taskbar)
    workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[2:4]

    for winID, winInfo in windowsInfo.items():
        if winID == windowID_active or winInfo['desktop'] != desktop:
            continue

        if moved_border == 1:  # left border
            # check, whether the windows were docked,
            # before the geometry of the active window changed
            if abs(winInfo['x2'] + 1 - winInfo_active['x']) <= tilingInfo['margin'] + tolerance and \
                    winInfo_active['y'] <= max(winInfo['y'], 0) + tolerance and \
                    winInfo_active['y2'] >= min(winInfo['y2'], workAreaHeight) - tolerance:
                geometry = get_window_geometry(window_active)
                if geometry is None:  # window vanished
                    return
                newWidth = geometry.x - winInfo['x']
                if newWidth >= tilingInfo['minSize']:
                    # resize, according to the new geometry of the active window
                    set_window_size(winID, width=newWidth)
                    disp.sync()
                    # update_windows_info()

        elif moved_border == 2:  # upper border
            # check, whether the windows were docked,
            # before the geometry of the active window  got changed
            if abs(winInfo['y2'] + 1 - winInfo_active['y']) <= tilingInfo['margin'] + tolerance and \
                    winInfo_active['x'] <= max(winInfo['x'], 0) + tolerance and \
                    winInfo_active['x2'] >= min(winInfo['x2'], workAreaWidth) - tolerance:
                geometry = get_window_geometry(window_active)
                if geometry is None:  # window vanished
                    return
                newHeight = geometry.y - winInfo['y']
                if newHeight >= tilingInfo['minSize']:
                    # resize, according to the new geometry of the active window
                    set_window_size(winID, height=newHeight)
                    disp.sync()
                    # update_windows_info()

        elif moved_border == 4:  # right border
            if abs(winInfo_active['x2'] + 1 - winInfo['x']) <= tilingInfo['margin'] + tolerance and \
                    winInfo_active['y'] <= max(winInfo['y'], 0) + tolerance and \
                    winInfo_active['y2'] >= min(winInfo['y2'], workAreaHeight) - tolerance:
                winActiveGeom = get_window_geometry(window_active)
                if winActiveGeom is None:  # window vanished
                    return
                winActive_x2 = winActiveGeom.x + winActiveGeom.width - 1
                newWidth = winInfo['x2'] - winActive_x2
                if newWidth >= tilingInfo['minSize']:
                    set_window_position(winID, x=winActive_x2 + 1)
                    set_window_size(winID, width=newWidth)
                    disp.sync()
                    # update_windows_info()

        elif moved_border == 8:  # lower border
            if abs(winInfo_active['y2'] + 1 - winInfo['y']) <= tilingInfo['margin'] + tolerance and \
                    winInfo_active['x'] <= max(winInfo['x'], 0) + tolerance and \
                    winInfo_active['x2'] >= min(winInfo['x2'], workAreaWidth) - tolerance:
                winActiveGeom = get_window_geometry(window_active)
                if winActiveGeom is None:  # window vanished
                    return
                winActive_y2 = winActiveGeom.y + winActiveGeom.height - 1
                newHeight = winInfo['y2'] - winActive_y2
                if newHeight >= tilingInfo['minSize']:
                    set_window_position(winID, y=winActive_y2 + 1)
                    set_window_size(winID, height=newHeight)
                    disp.sync()
                    # update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def set_setxy_win(winID):
    """
    For some applications/windows the positioning works fine when using their parent window,
    while for other applications this works when using their own window.
    This function determines which of these windows to take for this operation
    and stores this information in the windowsInfo - dictionary.
    As a test, the function tries to temporarily move the window down by one pixel,
    retrieves the actual position and then places it back.

    Unfortunately the awesome emwh (https://github.com/parkouss/pyewmh) doesn't seem to be
    a good option here, since it's use for resizing leads to a very annoying flickering
    of some applications (e.g. the Vivaldi-browser) while resizing them.
    TODO: Find a more elegant solution

    :param winID:  windows-ID
    :return:
    """
    global windowsInfo

    try:
        if windowsInfo[winID]['winSetXY'] is not None:
            return  # already set
    except KeyError:
        return

    try:
        unmaximize_window(windowsInfo[winID]['win'])
        oldY = windowsInfo[winID]['y']
        oldX = windowsInfo[winID]['x']
        windowsInfo[winID]['winParent'].configure(y=oldY + 1)
        disp.sync()

        time.sleep(0.05)
        newGeom = get_window_geometry(windowsInfo[winID]['winParent'])
        if abs(oldY + 1 - newGeom.y) <= 1:
            windowsInfo[winID]['winSetXY'] = windowsInfo[winID]['winParent']
        else:
            windowsInfo[winID]['winSetXY'] = windowsInfo[winID]['win']

        # restore old position
        windowsInfo[winID]['winSetXY'].configure(x=oldX, y=oldY)
        disp.sync()
    except (Xlib.error.BadWindow, AttributeError, KeyError) as e:
        pass  # window vanished
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def set_window_decoration(winID, status):
    """
    Undecorate / decorate the given window (title-bar and border)

    :param winID:   ID of the window
    :param status:  controls whether to show the decoration (True | False)
    :return:
    """
    global windowsInfo, MOTIF_WM_HINTS, ANY_PROPERTYTYPE

    try:
        window = windowsInfo[winID]['win']
        if (result := window.get_property(MOTIF_WM_HINTS, ANY_PROPERTYTYPE, 0, 32)):
            hints = result.value
            if hints[2] == int(status):
                return
            hints[2] = int(status)
        else:
            hints = (2, 0, int(status), 0, 0)
        window.change_property(MOTIF_WM_HINTS, MOTIF_WM_HINTS, 32, hints)
        set_window_position(winID, x=windowsInfo[winID]['x'], y=windowsInfo[winID]['y'])
        set_window_size(winID, width=windowsInfo[winID]['width'], height=windowsInfo[winID]['height'])
    except:
        pass
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def set_window_focus(windowID_active, window_active, direction='left'):
    """
    Make another window the active one.
    Move the focus from the currently active window to the next adjacent one
    in the given direction.
    Metric: Distance in the given direction  plus
            half of the distance in the orthogonal direction

    :param windowID_active:  ID of active window
    :param window_active:    active window
    :param direction:        'left', 'right', 'up' or 'down'
    :return:                 windowID_active, window_active
    """
    global windowsInfo, disp

    # get a list of all -not minimized and not ignored- windows of the current desktop
    desktop = windowsInfo[windowID_active]['desktop']
    winIDs = get_windows_on_desktop(desktop)

    if len(winIDs) < 2:
        return

    winID_next = None
    bestDistance = 1E99

    x_active = windowsInfo[windowID_active]['x']
    y_active = windowsInfo[windowID_active]['y']

    for winID in winIDs:
        if winID == windowID_active:
            continue

        x = windowsInfo[winID]['x']
        y = windowsInfo[winID]['y']
        distance = 1E99

        if direction == 'up':
            if y_active <= y:
                continue
            else:
                distance = y_active - y + abs(x - x_active)/2
        elif direction == 'down':
            if y_active >= y:
                continue
            else:
                distance = y - y_active + abs(x - x_active)/2
        elif direction == 'right':
            if x_active >= x:
                continue
            else:
                distance = x - x_active + abs(y - y_active)/2
        elif direction == 'left':
            if x_active <= x:
                continue
            else:
                distance = x_active - x + abs(y - y_active)/2

        if distance < bestDistance:
            bestDistance = distance
            winID_next = winID

    if winID_next:
        # set focus and make shure the window is in foreground
        windowsInfo[winID_next]["win"].set_input_focus(Xlib.X.RevertToParent, 0)
        windowsInfo[winID_next]["win"].configure(stack_mode=Xlib.X.Above)
        # update windowID_active and window_active, to inform function run()
        windowID_active = winID_next
        window_active = disp.create_resource_object('window', windowID_active)

    return windowID_active, window_active
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def set_window_position(winID, **kwargs):
    """
    Sets the position of the window.

    :param winID:    ID of the window
    :param kwargs:   x- and/or y-position
    :return:
    """
    global windowsInfo, MOTIF_WM_HINTS, ANY_PROPERTYTYPE

    # Check whether the window is undecorated, and if so, always use the window itself -i.e. not the parent window-
    # to configure the window position
    try:
        window = windowsInfo[winID]['win']
        if window.get_property(MOTIF_WM_HINTS, ANY_PROPERTYTYPE, 0, 32).value[2] == 0:
            windowsInfo[winID]['win'].configure(**kwargs)
            return
    except (AttributeError, KeyError) as e:
        return  # window vanished

    # Window is decorated, for some windows the parent window needs to be used  -  see function set_setxy_win()
    try:
        windowsInfo[winID]['winSetXY'].configure(**kwargs)
    except (AttributeError, KeyError) as e:
        set_setxy_win(winID)
        try:
            windowsInfo[winID]['winSetXY'].configure(**kwargs)
        except (AttributeError, KeyError) as e:
            pass  # window vanished
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def set_window_size(winID, **kwargs):
    """
    Sets the size of the window.

    :param winID:    ID of the window
    :param kwargs:   width and/or height
    :return:
    """
    global windowsInfo

    try:
        windowsInfo[winID]['winParent'].configure(**kwargs)
    except KeyError as e:
        pass
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def store_window_geometries():
    """
    Saves  the geometry of all -not minimized- windows of the current desktop
    :return:
    """
    global tilingInfo, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    # get a list of all -not minimized and not ignored- windows of the current desktop
    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    winIDs = get_windows_on_desktop(currentDesktop)

    tilingInfo['userDefinedGeom'][currentDesktop] = dict()
    for winID in winIDs:
        tilingInfo['userDefinedGeom'][currentDesktop][winID] = dict()
        tilingInfo['userDefinedGeom'][currentDesktop][winID]['x'] = windowsInfo[winID]['x']
        tilingInfo['userDefinedGeom'][currentDesktop][winID]['y'] = windowsInfo[winID]['y']
        tilingInfo['userDefinedGeom'][currentDesktop][winID]['width'] = windowsInfo[winID]['width']
        tilingInfo['userDefinedGeom'][currentDesktop][winID]['height'] = windowsInfo[winID]['height']

    notify('storeCurrentWindowsLayout')
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def swap_windows(winID):
    """
    Swap the position of window winID with the upper- / left-most window
    :param      winID:  ID of the window which should be moved
    :return:
    """
    global disp, Xroot, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    # get a list of all -not minimized and not ignored- windows of the current desktop
    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    winIDs = get_windows_on_desktop(currentDesktop)

    if len(winIDs) < 2:
        return

    # sort winIDs: first by y- then by x-position
    winIDs = sorted(winIDs, key=lambda winID: (windowsInfo[winID]['y'], windowsInfo[winID]['x']))

    if winID == winIDs[0]:
        return  # selected window is the top- / left- most one

    try:
        set_window_position(winID, x=windowsInfo[winIDs[0]]['x'], y=windowsInfo[winIDs[0]]['y'])
        set_window_size(winID, width=windowsInfo[winIDs[0]]['width'], height=windowsInfo[winIDs[0]]['height'])
        set_window_position(winIDs[0], x=windowsInfo[winID]['x'], y=windowsInfo[winID]['y'])
        set_window_size(winIDs[0], width=windowsInfo[winID]['width'], height=windowsInfo[winID]['height'])

        disp.sync()
        update_windows_info()
    except:
        pass  # window vanished
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows(manuallyTriggered=False, tilerNumber=None, desktopList=None, resizeMaster=0):
    """
    Calls the current or manually selected tiler
    for the current desktop, or -if given- for the desktops in desktopList

    :param manuallyTriggered:  status, whether called automatically or manually
    :param tilerNumber:        number which tiler to set and use,
                               or None, to take the currently selected tiler,
                               or 'next' to cycle to next tiler
    :param desktopList:        list of desktops where tiling needs to be done
    :param resizeMaster:       number of pixels the master should be resized
    :return:
    """
    global Xroot, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    if desktopList is None:
        currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
        desktopList = [currentDesktop]

    for desktop in desktopList:
        if not manuallyTriggered and not tilingInfo['tileWindows'][desktop]:
            continue

        if manuallyTriggered:
            if tilerNumber == 'next':
                tilingInfo['tiler'][desktop] = [2,3,4,5,1][tilingInfo['tiler'][desktop]-1]
            else:
                tilingInfo['tiler'][desktop] = tilerNumber

        if resizeMaster != 0 and tilingInfo['tiler'][desktop] not in [1, 3]:
            continue  # no tiler with a master window

        if tilingInfo['tiler'][desktop] == 1:
            tile_windows_master_and_stack_vertically(desktop, resizeMaster)
        elif tilingInfo['tiler'][desktop] == 2:
            tile_windows_vertically(desktop)
        elif tilingInfo['tiler'][desktop] == 3:
            tile_windows_master_and_stack_horizontally(desktop, resizeMaster)
        elif tilingInfo['tiler'][desktop] == 4:
            tile_windows_horizontally(desktop)
        elif tilingInfo['tiler'][desktop] == 5:
            tile_windows_maximize(desktop)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows_horizontally(desktop):
    """
    Stacks the -not minimized- windows of the given desktop horizontally, from left to right

    :param desktop:     desktop
    :return:
    """
    global tilingInfo, disp, Xroot, NET_WORKAREA

    # get a list of all -not minimized and not ignored- windows of the current desktop
    winIDs = get_windows_on_desktop(desktop)

    if len(winIDs) == 0:
        return

    # geometry of work area (screen without taskbar)
    workAreaX0, workAreaY0, workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[:4]

    if len(winIDs) == 1:
        set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
        if tilingInfo['maximizeWhenOneWindowLeft'][desktop]:
            set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
            set_window_size(winIDs[0], width=workAreaWidth, height=workAreaHeight)
            disp.sync()
            update_windows_info()
        return

    # sort the winIDs by x-position
    winIDs = sorted(winIDs, key=lambda winID: windowsInfo[winID]['x'])
    N = min(tilingInfo['horizontally']['maxNumWindows'], len(winIDs))

    set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
    # check whether this window can stay as it is
    if windowsInfo[winIDs[0]]['x'] == workAreaX0 and windowsInfo[winIDs[0]]['y'] == workAreaY0 and \
            windowsInfo[winIDs[0]]['y2'] - workAreaY0 == workAreaHeight - 1 and \
            tilingInfo['minSize'] < windowsInfo[winIDs[0]]['x2'] - workAreaX0 < \
                workAreaWidth - (N - 1) * tilingInfo['minSize']:
        set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
        I = 1
        x = windowsInfo[winIDs[0]]['x2'] + 1
        width = int((workAreaWidth - x) / (N - 1))
    else:
        I = 0
        x = workAreaX0
        width = int(workAreaWidth / N)

    # Place (all or remaining) windows from left to right (max. maxNumWindows)
    y = workAreaY0
    for i, winID in enumerate(winIDs[I:N]):
        if i == N - 1:
            width = workAreaWidth + workAreaX0 - x + 1
        unmaximize_window(windowsInfo[winID]["win"])
        set_window_decoration(winID, tilingInfo['windowDecoration'][desktop])
        set_window_position(winID, x=x, y=workAreaY0)
        set_window_size(winID, width=width, height=workAreaHeight + 1)
        x += width

    # make sure that all remaining (ignored) windows are decorated
    for winID in winIDs[N:]:
        set_window_decoration(winID, True)

    disp.sync()
    update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows_master_and_stack_horizontally(desktop, resizeMaster=0):
    """
    Tiles the -not minimized- windows of the given desktop,
    one master on the upper and a stack of windows (from left to right) on the lower part of the screen

    :param desktop:      desktop
    :param resizeMaster  number of pixels the master window should be resized
    :return:
    """
    global tilingInfo, disp, Xroot, NET_WORKAREA

    # get a list of all -not minimized and not ignored- windows of the given desktop
    winIDs = get_windows_on_desktop(desktop)

    if len(winIDs) == 0:
        return

    # geometry of work area (screen without taskbar)
    workAreaX0, workAreaY0, workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[:4]

    if len(winIDs) == 1:
        set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
        if tilingInfo['maximizeWhenOneWindowLeft'][desktop]:
            set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
            set_window_size(winIDs[0], width=workAreaWidth, height=workAreaHeight)
            disp.sync()
            update_windows_info()
        return

    # sort winIDs: first by y- then by x-position
    winIDs = sorted(winIDs, key=lambda winID: (windowsInfo[winID]['y'], windowsInfo[winID]['x']))

    set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
    # Place first window as master on the upper part of the screen
    if windowsInfo[winIDs[0]]['x'] == workAreaX0 and windowsInfo[winIDs[0]]['y'] == workAreaY0 and \
            windowsInfo[winIDs[0]]['x2'] - workAreaX0 == workAreaWidth - 1 and \
            tilingInfo['minSize'] < windowsInfo[winIDs[0]]['y2'] - workAreaY0 < workAreaHeight - tilingInfo['minSize']:
        # window can stay as it is
        set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
        height = windowsInfo[winIDs[0]]['height']
        if resizeMaster != 0:
            height += resizeMaster
            height = max(height, tilingInfo['minSize'] + 2)
            height = min(height, workAreaHeight - tilingInfo['minSize'])
            set_window_size(winIDs[0], width=workAreaWidth, height=height)
            resize_docked_windows(winIDs[0], windowsInfo[winIDs[0]]['win'], 8)
            disp.sync()
            update_windows_info()
            return
    else:
        # the window needs to be repositioned
        unmaximize_window(windowsInfo[winIDs[0]]["win"])
        height = int(workAreaHeight * tilingInfo['masterAndStackHoriz']['defaultHeightMaster'])
        set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
        set_window_size(winIDs[0], width=workAreaWidth, height=height)

    # Stack the remaining windows (max. maxNumWindows - 1) on the lower part of the screen
    N = min(tilingInfo['masterAndStackHoriz']['maxNumWindows'] - 1, len(winIDs) - 1)
    x = workAreaX0
    y = height + workAreaY0
    height = workAreaHeight - height
    width = int(workAreaWidth / N)

    for i, winID in enumerate(winIDs[1:N + 1]):
        if i == N - 1:
            width = workAreaWidth + workAreaX0 - x + 1
        unmaximize_window(windowsInfo[winID]["win"])
        set_window_decoration(winID, tilingInfo['windowDecoration'][desktop])
        set_window_position(winID, x=x, y=y)
        set_window_size(winID, width=width, height=height)
        x += width

    # make sure that all remaining (ignored) windows are decorated
    for winID in winIDs[N + 1:]:
        set_window_decoration(winID, True)

    disp.sync()
    update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows_master_and_stack_vertically(desktop, resizeMaster=0):
    """
    Tiles the -not minimized- windows of the given desktop,
    one master on the left and a stack of windows on the right (from top to bottom)

    :param desktop:       desktop
    :param resizeMaster:  number of pixels the master window should be resized
    :return:
    """
    global tilingInfo, disp, Xroot, NET_WORKAREA

    # get a list of all -not minimized and not ignored- windows of the current desktop
    winIDs = get_windows_on_desktop(desktop)

    if len(winIDs) == 0:
        return

    # geometry of work area (screen without taskbar)
    workAreaX0, workAreaY0, workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[:4]

    if len(winIDs) == 1:
        set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
        if tilingInfo['maximizeWhenOneWindowLeft'][desktop]:
            set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
            set_window_size(winIDs[0], width=workAreaWidth, height=workAreaHeight)
            disp.sync()
            update_windows_info()
        return

    # sort winIDs: first by x- then by y-position
    winIDs = sorted(winIDs, key=lambda winID: (windowsInfo[winID]['x'], windowsInfo[winID]['y']))

    set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
    # Place first window as master on the left side of the screen
    if windowsInfo[winIDs[0]]['x'] == workAreaX0 and windowsInfo[winIDs[0]]['y'] == workAreaY0 and \
            windowsInfo[winIDs[0]]['y2'] - workAreaY0 == workAreaHeight - 1 and \
            tilingInfo['minSize'] < windowsInfo[winIDs[0]]['x2'] - workAreaX0 < workAreaWidth - tilingInfo['minSize']:
        # the window can stay there
        set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
        width = windowsInfo[winIDs[0]]['width']
        if resizeMaster != 0:
            width += resizeMaster
            width = max(width, tilingInfo['minSize'] + 2)
            width = min(width, workAreaWidth - tilingInfo['minSize'])
            set_window_size(winIDs[0], width=width, height=workAreaHeight)
            resize_docked_windows(winIDs[0], windowsInfo[winIDs[0]]['win'], 4)
            disp.sync()
            update_windows_info()
            return
    else:
        # the window needs to be repositioned
        unmaximize_window(windowsInfo[winIDs[0]]['win'])
        width = int(workAreaWidth * tilingInfo['masterAndStackVertic']['defaultWidthMaster']) + resizeMaster
        set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
        set_window_size(winIDs[0], width=width, height=workAreaHeight)

    # Stack the remaining windows (max. maxNumWindows - 1) on the right part of the screen
    N = min(tilingInfo['masterAndStackVertic']['maxNumWindows'] - 1, len(winIDs) - 1)
    x = width + workAreaX0
    y = workAreaY0
    width = workAreaWidth - width
    height = int(workAreaHeight / N)

    for i, winID in enumerate(winIDs[1:N + 1]):
        if i == N - 1:
            height = workAreaHeight + workAreaY0 - y + 1
        unmaximize_window(windowsInfo[winID]["win"])
        set_window_decoration(winID, tilingInfo['windowDecoration'][desktop])
        set_window_position(winID, x=x, y=y)
        set_window_size(winID, width=width, height=height)
        y += height

    # make sure that all remaining (ignored) windows are decorated
    for winID in winIDs[N + 1:]:
        set_window_decoration(winID, True)

    disp.sync()
    update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows_maximize(desktop, winID=None):
    """
    This 'tiler' just maximizes the active window

    :param desktop:    desktop (given for possible future use)
    :param winID:      ID of the window, if None: retrieve ID of active window
    :return:
    """
    global Xroot, disp, NET_WM_STATE_MAXIMIZED_VERT, NET_WM_STATE_MAXIMIZED_HORZ, NET_WM_STATE, ANY_PROPERTYTYPE

    # geometry of work area (screen without taskbar)
    workAreaX0, workAreaY0, workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[:4]

    if winID is None:
        winID = Xroot.get_full_property(NET_ACTIVE_WINDOW, ANY_PROPERTYTYPE).value[0]
    set_window_decoration(winID, tilingInfo['windowDecoration'][desktop])
    set_window_position(winID, x=workAreaX0, y=workAreaY0)
    set_window_size(winID, width=workAreaWidth, height=workAreaHeight)

    mask = (Xlib.X.SubstructureRedirectMask | Xlib.X.SubstructureNotifyMask)
    event = Xlib.protocol.event.ClientMessage(window=window_active, client_type=NET_WM_STATE,
                                              data=(32, [1, NET_WM_STATE_MAXIMIZED_VERT, 0, 1, 0]))
    Xroot.send_event(event, event_mask=mask)
    event = Xlib.protocol.event.ClientMessage(window=window_active, client_type=NET_WM_STATE,
                                              data=(32, [1, NET_WM_STATE_MAXIMIZED_HORZ, 0, 1, 0]))
    Xroot.send_event(event, event_mask=mask)
    disp.flush()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def tile_windows_vertically(desktop):
    """
    Stacks the -not minimized- windows of the given desktop vertically, from top to bottom

    :param desktop:  desktop
    :return:
    """
    global tilingInfo, disp, Xroot, NET_WORKAREA

    # get a list of all -not minimized and not ignored- windows of the current desktop
    winIDs = get_windows_on_desktop(desktop)

    if len(winIDs) == 0:
        return

    # geometry of work area (screen without taskbar)
    workAreaX0, workAreaY0, workAreaWidth, workAreaHeight = Xroot.get_full_property(NET_WORKAREA, 0).value.tolist()[:4]

    if len(winIDs) == 1:
        set_window_decoration(winIDs[0], tilingInfo['windowDecoration'][desktop])
        if tilingInfo['maximizeWhenOneWindowLeft'][desktop]:
            set_window_position(winIDs[0], x=workAreaX0, y=workAreaY0)
            set_window_size(winIDs[0], width=workAreaWidth, height=workAreaHeight)
            disp.sync()
            update_windows_info()
        return

    # sort the winIDs by y-position
    winIDs = sorted(winIDs, key=lambda winID: windowsInfo[winID]['y'])

    # Stack windows (max. maxNumWindows)
    N = min(tilingInfo['vertically']['maxNumWindows'], len(winIDs))
    y = workAreaY0
    height = int(workAreaHeight / N)
    for i, winID in enumerate(winIDs[:N]):
        if i == N - 1:
            height = workAreaHeight + workAreaY0 - y + 1
        unmaximize_window(windowsInfo[winID]['win'])
        set_window_decoration(winID, tilingInfo['windowDecoration'][desktop])
        set_window_position(winID, x=workAreaX0, y=y)
        set_window_size(winID, width=workAreaWidth, height=height)
        y += height

    # make sure that all remaining (ignored) windows are decorated
    for winID in winIDs[N:]:
        set_window_decoration(winID, True)

    disp.sync()
    update_windows_info()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def toggle_maximize_when_one_window():
    """
    Toggles whether a tiling should maximize a window when it's the only one on the current desktop
    :return:
    """
    global tilingInfo, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    tilingInfo['maximizeWhenOneWindowLeft'][currentDesktop] = \
        not tilingInfo['maximizeWhenOneWindowLeft'][currentDesktop]

    notify('maximizeWhenOneWindowLeft', tilingInfo["maximizeWhenOneWindowLeft"][currentDesktop])
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def toggle_resize():
    """
    Toggles whether resizing of docked windows is active for the current desktop
    :return:
    """
    global tilingInfo, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    tilingInfo['resizeWindows'][currentDesktop] = not tilingInfo['resizeWindows'][currentDesktop]

    notify('resizing', tilingInfo["resizeWindows"][currentDesktop])
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def toggle_tiling():
    """
    Toggles whether tiling is active for the current desktop

    :return: new tiling state of the current desktop [True | False]
    """
    global tilingInfo, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    tilingInfo['tileWindows'][currentDesktop] = not tilingInfo['tileWindows'][currentDesktop]

    notify('tiling', tilingInfo["tileWindows"][currentDesktop])
    return tilingInfo['tileWindows'][currentDesktop]
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def toggle_window_decoration():
    """
    Toggles whether tiled windows on the current desktop should be decorated.
    The remaining, ignored windows are always (re-)decorated.
    :return:
    """
    global tilingInfo, NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE

    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
    status = tilingInfo['windowDecoration'][currentDesktop] = not tilingInfo['windowDecoration'][currentDesktop]
    update_windows_info()
    for winID in windowsInfo:
        if windowsInfo[winID]['desktop'] == currentDesktop:
            set_window_decoration(winID, status)
    disp.sync()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def unmaximize_window(window):
    """
    Un-maximize the given window

    :param window:  the window
    :return:
    """
    global Xroot, disp, NET_WM_STATE_MAXIMIZED_VERT, NET_WM_STATE_MAXIMIZED_HORZ, NET_WM_STATE

    mask = (Xlib.X.SubstructureRedirectMask | Xlib.X.SubstructureNotifyMask)
    event = Xlib.protocol.event.ClientMessage(window=window, client_type=NET_WM_STATE,
                                              data=(32, [0, NET_WM_STATE_MAXIMIZED_VERT, 0, 1, 0]))
    Xroot.send_event(event, event_mask=mask)
    event = Xlib.protocol.event.ClientMessage(window=window, client_type=NET_WM_STATE,
                                              data=(32, [0, NET_WM_STATE_MAXIMIZED_HORZ, 0, 1, 0]))
    Xroot.send_event(event, event_mask=mask)

    disp.flush()
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def update_windows_info():
    """
    Update the dictionary containing all windows, parent-windows, names, desktop-number and geometry.
    Windows with names / titles that match the ignore-list and modal and sticky windows are not taken into account.

    :return: status           whether the number of windows has changed,  and
             desktopList      list of desktops, when a window got moved from one desktop to another
    """
    global NET_CLIENT_LIST, NET_WM_DESKTOP, NET_WM_STATE_MODAL, NET_WM_STATE_STICKY, ANY_PROPERTYTYPE, Xroot, disp
    global tilingInfo, windowsInfo, verbosityLevel

    windowIDs = Xroot.get_full_property(NET_CLIENT_LIST, ANY_PROPERTYTYPE).value
    numWindowsChanged = False
    doDelay = False
    desktopList = list()

    # delete closed windows from the windowsInfo structure
    for winID in list(windowsInfo.keys()):
        if winID not in windowIDs:
            del windowsInfo[winID]
            numWindowsChanged = True

    # update the geometry of existing windows and add new windows
    for winID in windowIDs:
        try:
            try:
                win = windowsInfo[winID]['win']
            except KeyError:
                win = disp.create_resource_object('window', winID)

            if NET_WM_STATE_MODAL in win.get_full_property(NET_WM_STATE, 0).value.tolist():
                if verbosityLevel > 1:
                    title = get_windows_title(win)
                    winClass, name = win.get_wm_class()
                    print('Ignoring modal window:\t'
                          f'name: "{name}"\ttitle: "{title}"')
                continue  # ignore modal window (dialog box)

            if NET_WM_STATE_STICKY in win.get_full_property(NET_WM_STATE, 0).value.tolist():
                if verbosityLevel > 1:
                    title = get_windows_title(win)
                    winClass, name = win.get_wm_class()
                    print('Ignoring sticky window:\t'
                          f'name: "{name}"\ttitle: "{title}"')
                continue  # ignore sticky window (visible on all workspaces)

            if winID in windowsInfo or not match_ignore(tilingInfo['ignoreWindows'],
                                                        (name := win.get_wm_class()[1]),
                                                        get_windows_title(win)):
                desktop = win.get_full_property(NET_WM_DESKTOP, ANY_PROPERTYTYPE).value[0]
                geometry = get_window_geometry(win)
                if geometry is None:  # window vanished
                    continue
                if winID not in windowsInfo:
                    windowsInfo[winID] = dict()
                    windowsInfo[winID]['name'] = name
                    windowsInfo[winID]['win'] = win
                    windowsInfo[winID]['winParent'] = get_parent_window(win)
                    windowsInfo[winID]['winSetXY'] = None
                    numWindowsChanged = True
                    if match(tilingInfo['delayTilingWindowsWithNames'], name):
                        doDelay = True  # An app, that needs some delay, got launched
                try:
                    if windowsInfo[winID]['desktop'] != desktop:
                        numWindowsChanged = True  # Window was moved to another desktop
                        desktopList.append(desktop)  # Tiling (if activated) needs to be done
                        desktopList.append(windowsInfo[winID]['desktop'])  # on both desktops
                except KeyError:
                    pass
                windowsInfo[winID]['desktop'] = desktop
                windowsInfo[winID]['x'] = geometry.x
                windowsInfo[winID]['y'] = geometry.y
                windowsInfo[winID]['height'] = geometry.height
                windowsInfo[winID]['width'] = geometry.width
                windowsInfo[winID]['x2'] = geometry.x + geometry.width - 1
                windowsInfo[winID]['y2'] = geometry.y + geometry.height - 1
        except:
            pass  # window has vanished

    if doDelay:
        time.sleep(tilingInfo['delayTimeTiling'])

    return numWindowsChanged, set(desktopList)
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def write_crashlog():
    """
    Writes, respectively appends trace-back information into /tmp/xpytile_<USER>.log
    :return:
    """

    import traceback
    exc_type, exc_value, exc_traceback = sys.exc_info()
    exception_message = traceback.format_exception(exc_type, exc_value, exc_traceback)
    fileName = os.path.join('/tmp', f'xpytile_crash_{os.environ["USER"]}.log')
    with open(fileName, 'a') as f:
        dateStr = datetime.datetime.strftime(datetime.datetime.now(), '%x %X')
        f.write(f'[{dateStr}] {exception_message}\n')
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
def run(window_active, window_active_parent, windowID_active):
    """
    Waits for events (change of active window, hotkeys)
    and resizes docked windows and does a little bit of tiling

    :param window_active:         active window
    :param window_active_parent:  parent window of the active window
    :param windowID_active:       ID of the active window
    :return:
    """
    global disp, Xroot, NET_ACTIVE_WINDOW, NET_WM_DESKTOP, windowsInfo, tilingInfo, verbosityLevel
    global ANY_PROPERTYTYPE, NET_CURRENT_DESKTOP

    PROPERTY_NOTIFY = Xlib.X.PropertyNotify
    CONFIGURE_NOTIFY = Xlib.X.ConfigureNotify
    KEY_RELEASE = Xlib.X.KeyRelease

    tile_windows()
    while True:
        event = disp.next_event()  # sleep until an event occurs

        if event.type == PROPERTY_NOTIFY and event.atom in [NET_ACTIVE_WINDOW, NET_CURRENT_DESKTOP]:
            # the active window or the desktop has changed
            numWindowsChanged, desktopList = update_windows_info()
            windowID_active = Xroot.get_full_property(NET_ACTIVE_WINDOW, ANY_PROPERTYTYPE).value[0]
            window_active = disp.create_resource_object('window', windowID_active)
            window_active_parent = get_parent_window(window_active)

            if verbosityLevel > 0:
                if event.atom == NET_ACTIVE_WINDOW:
                    print('Active window has changed:\t'
                          f'name: "{get_windows_name(windowID_active, window_active)}"\t'
                          f'title: "{get_windows_title(window_active)}"'
                          f'{["", ", num. windows changed"][numWindowsChanged]}')
                else:
                    print(f'Desktop changed'
                          f'{["", ", num. windows changed"][numWindowsChanged]}')

            if desktopList:
                tile_windows(False, None, desktopList)
            elif numWindowsChanged:
                tile_windows()
            else:
                # The number of windows has not changed, neither the desktop,
                # but another window is active.  So if the maximize-'tiler'
                # is in action, the active window must be maximized.
                try:
                    currentDesktop = Xroot.get_full_property(NET_CURRENT_DESKTOP, ANY_PROPERTYTYPE).value[0]
                    if tilingInfo['tiler'][currentDesktop] == 5:
                        tile_windows(False, 0)  # maximize active window
                except:
                    pass
        elif event.type == CONFIGURE_NOTIFY and event.window == window_active_parent:
            moved_border = get_moved_border(windowID_active, window_active)
            if moved_border:
                resize_docked_windows(windowID_active, window_active, moved_border)
            update_windows_info()
        elif event.type == KEY_RELEASE:
            windowID_active, window_active = handle_key_event(event.detail, windowID_active, window_active)
# ----------------------------------------------------------------------------------------------------------------------


if __name__ == '__main__':
    configFile = 'xpytilerc'
    configPath = os.getenv('XDG_CONFIG_HOME')
    if configPath:
        configFilePath = os.path.join(configPath, configFile)
    else:
        configFilePath = os.path.join('~/.config/', configFile)

    # If there is no user-specific config-file, try to copy it from /etc
    if not os.path.exists(os.path.expanduser(configFilePath)):
        try:
            shutil.copyfile('/etc/xpytilerc', os.path.expanduser(configFilePath))
        except:
            write_crashlog()
            raise SystemExit('No config-file found')

    global verbosityLevel
    parser = argparse.ArgumentParser(prog='xpytile.py')
    parser.add_argument('-v', '--verbose', action="store_true", help='Print name and title of new windows')
    parser.add_argument('-vv', '--verbose2', action="store_true",
                        help='also print details about checking name and title whether to ignore the new window')
    args = parser.parse_args()
    if args.verbose2:
        verbosityLevel = 2
    elif args.verbose:
        verbosityLevel = 1
    else:
        verbosityLevel = 0

    # Create singleton using abstract socket (prefix with \0)
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind('\0xpytile_lock')
    except socket.error:
        config = configparser.ConfigParser()
        config.read(os.path.expanduser(configFilePath))
        init_notification_info(config)
        notify('alreadyRunning')
        raise SystemExit('xpytile already running, exiting.')

    try:
        # Initialize
        window_active, window_active_parent, windowID_active = init(configFilePath)
        # Run: wait for events and handle them
        run(window_active, window_active_parent, windowID_active)
    except KeyboardInterrupt:
        raise SystemExit(' terminated by ctrl-c')
    except SystemExit:
        pass
    except:
        # Something went wrong, write traceback info in /tmp
        write_crashlog()
