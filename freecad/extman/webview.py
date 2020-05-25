# -*- coding: utf-8 -*-
# ***************************************************************************
# *                                                                         *
# *  Copyright (c) 2020 Frank Martinez <mnesarco at gmail.com>              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *  This program is distributed in the hope that it will be useful,        *
# *  but WITHOUT ANY WARRANTY; without even the implied warranty of         *
# *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the          *
# *  GNU General Public License for more details.                           *
# *                                                                         *
# *  You should have received a copy of the GNU General Public License      *
# *  along with this program.  If not, see <https://www.gnu.org/licenses/>. *
# *                                                                         *
# ***************************************************************************

import FreeCADGui as Gui
import os
import re
from PySide import QtCore, QtGui
from PySide.QtCore import Qt
from PySide2.QtWebEngineCore import (QWebEngineUrlRequestInfo,
                                     QWebEngineUrlRequestInterceptor,
                                     QWebEngineUrlSchemeHandler)
from PySide2.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView, QWebEnginePage
from urllib.parse import unquote

from freecad.extman import utils, log

EXTMAN_URL_SCHEME = b'extman'                               # extman://...
WINDOWS_PATH_PATTERN = re.compile(r'^/([a-zA-Z]\\:.*)')     # /C:...
ACTION_URL_PATTERN = re.compile(r'.*/action\.(\w+)$')       # action.<name>


class UrlRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Intercepts extman://... links"""

    def __init__(self, parent):
        super().__init__()
        self.owner = parent

    def interceptRequest(self, info):
        if info.navigationType() == QWebEngineUrlRequestInfo.NavigationTypeLink:
            url = info.requestUrl()

            # Fix windows path
            if not url.host() and url.isLocalFile():
                m = WINDOWS_PATH_PATTERN.match(url.path())  # "/C:/something" -> "C:/something"
                if m:
                    url.setPath(m.group(1))

            # Filter
            if url.scheme() == 'extman':
                self.owner.interseptLink(info)


class Response(QtCore.QObject):

    def __init__(self, parent, buffer, request):
        super().__init__(parent=parent)
        self.buffer = buffer
        self.request = request

    def write(self, data):
        self.buffer.write(data.encode())

    def send(self, content_type='text/html'):
        self.buffer.seek(0)
        self.buffer.close()
        self.request.reply(content_type.encode(), self.buffer)


class SchemeHandler(QWebEngineUrlSchemeHandler):
    """
    Process all requests with schema = extman://
    """

    def __init__(self, schemaName, requestHandler):
        super().__init__(schemaName)
        self.requestHandler = requestHandler

    def requestStarted(self, request):

        # Parse Url
        url = request.requestUrl()
        path = url.path()
        query = QtCore.QUrlQuery(url)
        params = {k: unquote(v) for k, v in query.queryItems()}

        # Prepare response buffer
        buf = QtCore.QBuffer(parent=self)
        request.destroyed.connect(buf.deleteLater)
        buf.open(QtCore.QIODevice.WriteOnly)

        # Match Action
        action = None
        action_match = ACTION_URL_PATTERN.match(path)
        if action_match:
            action = action_match.group(1)

        if path.endswith('.html') or action:

            # Prepare Response object
            response = Response(self, buf, request)
            request.destroyed.connect(response.deleteLater)

            # Call handler to do the real work
            # ! Important: requestHandler can work in another thread.
            # !            response.send() should be called from the handler
            # !            to send any content.
            self.requestHandler(path, action, params, request, response)

        else:
            file_path = utils.fix_win_path(path)
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    file_path = path.lower()
                    buf.write(f.read())
                    buf.seek(0)
                    buf.close()
                    if file_path.endswith('.svg'):
                        content_type = 'image/svg+xml'
                    elif file_path.endswith('.png'):
                        content_type = 'image/png'
                    elif file_path.endswith('.jpg'):
                        content_type = 'image/jpeg'
                    elif file_path.endswith('.css'):
                        content_type = 'text/css'
                    elif file_path.endswith('.js'):
                        content_type = 'text/javascript'
                    else:
                        content_type = 'text/plain'
                    request.reply(content_type.encode(), buf)
            else:
                print("Path does not exists: " + file_path)


class Page(QWebEnginePage):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def javaScriptConsoleMessage(self, *args, **kwargs):
        pass


class WebView(QtGui.QMdiSubWindow):
    closed = QtCore.Signal(object)

    def __init__(self, title, workdir, requestHandler, *args, **kwargs):
        # Window Setup
        super().__init__(*args, **kwargs)
        self.setObjectName("freecad.extman.webview")
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        # Scheme setup (extman://)
        scheme = EXTMAN_URL_SCHEME
        self.handler = SchemeHandler(self, requestHandler)

        # Url Filter setup
        self.interceptor = UrlRequestInterceptor(self)

        # WebView setup
        self.webView = QWebEngineView(self)
        self.webView.setContextMenuPolicy(Qt.NoContextMenu)
        self.setWidget(self.webView)

        # Page setup
        self.webView.setPage(Page(self.webView))

        # Profile setup
        profile = self.webView.page().profile()
        profile.setPersistentStoragePath(os.path.join(workdir, 'persistent'))
        profile.setCachePath(os.path.join(workdir, 'cache'))
        profile.setRequestInterceptor(self.interceptor)
        handler = profile.urlSchemeHandler(scheme)
        if handler is not None:
            profile.removeUrlSchemeHandler(handler)
        profile.installUrlSchemeHandler(scheme, self.handler)

        # Setting setup
        settings = self.webView.settings()
        settings.setAttribute(QWebEngineSettings.AutoLoadIconsForPage, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

        # Page setup
        page = self.webView.page().settings()
        page.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True);
        page.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True);
        page.setAttribute(QWebEngineSettings.LocalStorageEnabled, True);

    def interseptLink(self, info):
        pass

    def closeEvent(self, event):
        self.closed.emit(event)
        event.accept()

    def load(self, url):
        self.webView.load(url)


# ! Call as soon as possible
def register_custom_schemes():
    try:
        from PySide2.QtWebEngineCore import QWebEngineUrlScheme
    except ImportError:
        log('Outdated QT version, some graphics will be not available')
    else:
        scheme = EXTMAN_URL_SCHEME
        scheme_reg = QWebEngineUrlScheme(scheme)
        scheme_reg.setFlags(
            QWebEngineUrlScheme.SecureScheme
            | QWebEngineUrlScheme.LocalScheme
            | QWebEngineUrlScheme.LocalAccessAllowed
            | QWebEngineUrlScheme.ContentSecurityPolicyIgnored
            | 0x80  # QWebEngineUrlScheme.CorsEnabled
        )
        QWebEngineUrlScheme.registerScheme(scheme_reg)

