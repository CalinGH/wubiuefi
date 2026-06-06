# Copyright (c) 2008 Agostino Russo
#
# Written by Agostino Russo <agostino.russo@gmail.com>
#
# This file is part of Wubi the Win32 Ubuntu Installer.
#
# Wubi is free software; you can redistribute it and/or modify
# it under 5the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation; either version 2.1 of
# the License, or (at your option) any later version.
#
# Wubi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from winui import ui
from .page import Page
import logging
log = logging.getLogger("WinuiInstallationFinishPage")


class InstallationFinishPage(Page):

    def on_init(self):
        Page.on_init(self)
        self.set_background_color(255,255,255)
        self.insert_vertical_image("%s-vertical.bmp" % self.info.distro.name)

        #navigation
        self.insert_navigation(_("Finish"), default=1)
        self.navigation.button1.on_click = self.on_finish

        #main container
        self.insert_main()
        self.main.set_background_color(255,255,255)
        self.main.title = ui.Label(self.main, 40, 20, self.main.width - 80, 60, _("Completing the %s Setup Wizard") % self.info.distro.name)
        self.main.title.set_font(size=20, bold=True, family="Arial")
        install_mode = getattr(self.info, 'install_mode', 'wubi') or 'wubi'
        if install_mode == 'autoinstall':
            label_text = _(
                "Wubi has staged the Ubuntu installer. When you reboot, Ubuntu "
                "will automatically install itself alongside Windows on a real "
                "partition. The process takes 10\u201320 minutes and your computer "
                "will restart again when it is complete.")
        elif install_mode == 'guided':
            label_text = _(
                "Wubi has prepared the boot environment. When you reboot, the "
                "Ubuntu live installer will start. Follow the on-screen steps to "
                "install Ubuntu alongside Windows on a real partition.")
        else:
            label_text = _("You need to reboot to complete the installation")
        self.main.label = ui.Label(self.main, 40, 90, self.main.width - 80, 60, label_text)
        self.main.reboot_now = ui.RadioButton(self.main, 60, 160, self.main.width - 100, 20, _("Reboot now"))
        self.main.reboot_later = ui.RadioButton(self.main, 60, 185, self.main.width - 100, 20, _("I want to manually reboot later"))
        self.main.reboot_later.set_check(True)

    def on_finish(self):
        if self.main.reboot_now.is_checked():
            self.info.run_task = "reboot"
        self.frontend.stop()

