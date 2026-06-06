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
from wubi.backends.common.mappings import reserved_usernames, lang_country2linux_locale, language2lang_country, lang_country2language
import os
import logging
import re
import gettext

log = logging.getLogger("WinuiInstallationPage")

reserved_usernames = [str(n) for n in reserved_usernames]
re_username_first = re.compile("^[a-z]")
re_username = re.compile("[a-z][-a-z0-9_]*$")

class InstallationPage(Page):

    def add_controls_block(self, parent, left, top, bmp, label, is_listbox):
        picture = ui.Bitmap(
            parent,
            left, top + 6, 32, 32)
        picture.set_image(
            os.path.join(str(self.info.image_dir), str(bmp)))
        label = ui.Label(
            parent,
            left + 32 + 10, top, 150, 12,
            label)
        if is_listbox:
            combo = ui.ComboBox(
                parent,
                left + 32 + 10, top + 20, 150, 200,
                "")
        else:
            combo = None
        return picture, label, combo

    def check_disk_free_space(self):
        if self.info.skip_size_check:
            return
        min_space_mb = self.info.distro.min_disk_space_mb + self.info.distro.max_iso_size/(1024**2)+ 100
        max_space_mb = 0
        max_space_mb2 = 0
        for drive in self.info.drives:
            if drive.type not in ['removable', 'hd']:
                continue
            max_space_mb = max(max_space_mb, drive.free_space_mb)
            if int(drive.free_space_mb/1024) * 1000 > min_space_mb:
                max_space_mb2 = max(max_space_mb2, drive.free_space_mb)
        if max_space_mb < 1024:
            message = _("Only %sMB of disk space are available.\nAt least 1024MB are required as a bare minimum. Quitting")
            self.frontend.show_error_message(message % int(max_space_mb))
            self.application.quit()
        if max_space_mb2 < min_space_mb:
            message = _("%(min_space)sMB of disk size are required for installation.\nOnly %(max_space)sMB are available.\nThe installation may fail in such circumstances.\nDo you wish to continue anyway?")
            min_space_mb = round(min_space_mb/1000+0.5)*1024
            message = message % dict(min_space=int(min_space_mb), max_space=int(max_space_mb))
            if not self.frontend.ask_confirmation(message):
                self.application.quit()
            else:
                self.info.skip_size_check = True

    def populate_drive_list(self):
        self.check_disk_free_space()
        min_space_mb = self.info.distro.min_disk_space_mb + self.info.distro.max_iso_size/(1024**2)+ 100
        self.drives_gb = []
        self.target_drive_list.clear()
        for drive in self.info.drives:
            if drive.type not in ['removable', 'hd']:
                continue
            drive_space_mb = int(drive.free_space_mb/1024) * 1000
            if self.info.skip_size_check \
            or drive_space_mb > min_space_mb:
                text = drive.path + " "
                text += _("(%sGB free)") % (drive_space_mb/1000)
                self.drives_gb.append(text)
                self.target_drive_list.add_item(text)
        self.select_default_drive()

    def select_default_drive(self):
        drive = self.info.target_drive
        if drive:
            Drive = self.info.drives[0].__class__
            if isinstance(drive, Drive):
                pass
            else:
                drive = self.info.drives_dict.get(drive[:2].lower())
        drive_text = self.drives_gb[0]
        if drive:
            for d in self.drives_gb:
                if d.startswith(drive.path):
                    drive_text = d
        self.target_drive_list.set_value(drive_text)
        self.on_drive_change()

    def populate_size_list(self):
        target_drive = self.get_drive()
        self.size_list_gb = []
        self.size_list.clear()
        i_size_list = list(range(1, 33)) + [64, 128, 256, 512]
        if self.info.installation_size_mb:
            i = int(self.info.installation_size_mb/1000)
            if i not in i_size_list:
               i_size_list.append(i)
               i_size_list.sort()
        for i in i_size_list:
            #~ log.debug("%s < %s and %s > %s" % (i * 1000 + self.info.distro.max_iso_size/1024**2 + 100 , target_drive.free_space_mb, i * 1000 , self.info.distro.min_disk_space_mb))
            if self.info.skip_size_check \
            or i * 1000 >= self.info.distro.min_disk_space_mb: #use 1000 as it is more conservative
                if i * 1000 + self.info.distro.max_iso_size/1024**2 + 100 <= target_drive.free_space_mb:
                    self.size_list_gb.append(i)
                    self.size_list.add_item("%sGB" % i)
        self.select_default_size()

    def select_default_size(self):
        if self.info.installation_size_mb:
            installation_size_gb = int(self.info.installation_size_mb/1000)
            for i in self.size_list_gb:
                if i >= installation_size_gb:
                    self.size_list.set_value("%sGB" % i)
                    return
        i = int(len(self.size_list_gb)/2)
        installation_size_gb = self.size_list_gb[i]
        self.size_list.set_value("%sGB" % installation_size_gb)
        self.on_size_change()

    def populate_distro_list(self):
        if self.info.cd_distro:
            distros = [self.info.cd_distro.name]
        elif self.info.iso_distro:
            distros = [self.info.iso_distro.name]
        else:
            distros = []
            for distro in self.info.distros:
                if distro.name not in distros:
                    distros.append(distro.name)
        for distro in distros:
            self.distro_list.add_item(distro)
        self.distro_list.set_value(distros[0])
        self.on_distro_change()

    def populate_language_list(self):
        languages = sorted(language2lang_country.keys())
        for language in languages:
            self.language_list.add_item(language)
        language = lang_country2language.get(self.info.language, None)
        if not language and self.info.windows_language in language2lang_country.keys():
            language = self.info.windows_language
        if not language:
            language = lang_country2language.get("en_US")
        self.language_list.set_value(language)

    def on_init(self):
        Page.on_init(self)

        #header
        #The title and image are overridden in on_distro_change, the following are stubs
        self.insert_header(
            "Installing",
            _("Please select username and password for the new account"),
            "header.bmp")

        #navigation
        self.insert_navigation(_("Accessibility"), _("Install"), _("Cancel"), default=2)
        self.navigation.button3.on_click = self.on_cancel
        self.navigation.button2.on_click = self.on_install
        self.navigation.button1.on_click = self.on_accessibility

        #Main control container
        self.insert_main()
        h=24
        w=150

        picture, label, self.target_drive_list = self.add_controls_block(
            self.main, h, h,
            "install.bmp", _("Installation drive:"), True)
        # populated by on_distro_change
        self.target_drive_list.on_change = self.on_drive_change

        self.size_picture, self.size_label_widget, self.size_list = self.add_controls_block(
                self.main, h, h*4,
                "disksize.bmp", _("Installation size:"), True)
        # populated by on_drive_change
        self.size_list.on_change = self.on_size_change

        picture, label, self.distro_list = self.add_controls_block(
            self.main, h, h*7,
            "desktop.bmp", _("Desktop environment:"), True)
        self.populate_distro_list()
        self.distro_list.on_change = self.on_distro_change

        # Let the user point Wubi at an ISO they already downloaded.
        self.browse_iso_button = ui.Button(
            self.main,
            h + 32 + 10, h*7 + 44, 150, 24,
            _("Browse for ISO..."))
        self.browse_iso_button.on_click = self.on_browse_iso

        # Installation mode selector: Wubi loop-file, automated real-partition
        # (subiquity autoinstall), or guided (live installer launched manually).
        mode_top = h*7 + 44 + 32
        ui.Label(
            self.main,
            h + 32 + 10, mode_top, 280, 12,
            _("Installation type:"))
        self.mode_wubi = ui.RadioButton(
            self.main,
            h + 32 + 10, mode_top + 14, 310, 18,
            _("Wubi — Ubuntu inside Windows (no repartitioning, easy to remove)"))
        self.mode_autoinstall = ui.RadioButton(
            self.main,
            h + 32 + 10, mode_top + 33, 310, 18,
            _("Install alongside Windows — real partition (automated, uses Ubuntu installer)"))
        self.mode_guided = ui.RadioButton(
            self.main,
            h + 32 + 10, mode_top + 52, 310, 18,
            _("Launch Ubuntu installer manually — real partition (guided)"))
        self.mode_wubi.on_click = self.on_mode_change
        self.mode_autoinstall.on_click = self.on_mode_change
        self.mode_guided.on_click = self.on_mode_change

        current_mode = getattr(self.info, 'install_mode', 'wubi') or 'wubi'
        if current_mode == 'autoinstall':
            self.mode_autoinstall.set_check(True)
        elif current_mode == 'guided':
            self.mode_guided.set_check(True)
        else:
            self.mode_wubi.set_check(True)
        self.on_mode_change()

        picture, label, self.language_list = self.add_controls_block(
            self.main, h*4 + w, h,
            "language.bmp", _("Language:"), True)
        self.populate_language_list()
        self.language_list.on_change = self.on_language_change

        if self.info.username:
            username = self.info.username
        else:
            username = self.info.host_username
        username = re.sub('[^-a-z0-9_]', '', username.strip().lower())
        picture, label, combo = self.add_controls_block(
            self.main, h*4 + w, h*4,
            "user.bmp", _("Username:"), None)
        self.username = ui.Edit(
            self.main,
            h*4 + w + 42, h*4+20, 150, 20,
            username, False)

        picture, label, combo = self.add_controls_block(
            self.main, h*4 + w, h*7,
            "lock.bmp", _("Password:"), None)
        label.move(h*4 + w + 42, h*7 - 24)
        password = ""
        if self.info.password:
            password = self.info.password
        elif self.info.test:
            password = "test"
        self.password1 = ui.PasswordEdit(
            self.main,
            h*4 + w + 42, h*7-4, 150, 20,
            password, False)
        self.password2 = ui.PasswordEdit(
            self.main,
            h*4 + w + 42, h*7+20, 150, 20,
            password, False)
        self.error_label = ui.Label(
            self.main,
            40, self.main.height - 20, self.main.width - 80, 12,
            "")
        self.error_label.set_text_color(255, 0, 0)

        if self.info.non_interactive:
            self.on_install()

    def get_drive(self):
        target_drive = self.target_drive_list.get_text()[:2].lower()
        drive = self.info.drives_dict.get(target_drive)
        return drive

    def get_installation_size_mb(self):
        installation_size = self.size_list.get_text()
        #using 1000 as opposed to 1024
        installation_size = int(installation_size[:-2])*1000
        return installation_size

    def on_distro_change(self):
        distro_name = str(self.distro_list.get_text())
        self.info.distro = self.info.distros_dict.get((distro_name.lower(), self.info.arch))
        # Fall through to i386 if an amd64 version of a particular distribution
        # does not exist.
        if not self.info.distro and self.info.arch == 'amd64':
            self.info.distro = self.info.distros_dict.get((distro_name.lower(), 'i386'))
        self.frontend.set_title(_("%s Installer") % self.info.distro.name)
        bmp_file = "%s-header.bmp" % self.info.distro.name
        self.header.image.set_image(os.path.join(str(self.info.image_dir), str(bmp_file)))
        self.header.title.set_text(_("You are about to install %(distro)s-%(version)s") % dict(distro=self.info.distro.name, version=self.info.version))
        icon_file = "%s.ico" % self.info.distro.name
        self.frontend.set_icon(os.path.join(str(self.info.image_dir), str(icon_file)))
        if not self.info.skip_memory_check:
            if self.info.total_memory_mb < self.info.distro.min_memory_mb:
                message = _("%(min_memory)sMB of memory are required for installation.\nOnly %(total_memory)sMB are available.\nThe installation may fail in such circumstances.\nDo you wish to continue anyway?")
                message = message % dict(min_memory=int(self.info.distro.min_memory_mb), total_memory=int(self.info.total_memory_mb))
                if not self.frontend.ask_confirmation(message):
                    self.application.quit()
                else:
                    self.info.skip_memory_check = True
        self.populate_drive_list()

    def on_language_change(self):
        language = self.language_list.get_text()
        language1 = language2lang_country.get(language, None)
        language2 = language1 and language1.split('_')[0]
        language3 = lang_country2linux_locale.get(self.info.language, None)
        language4 = language3 and language3.split('.')[0]
        language5 = language4 and language4.split('_')[0]
        translation = gettext.translation(self.info.application_name, localedir=self.info.translations_dir, languages=[language1, language2, language3, language4, language5])
        translation.install(names=[ngettext])

    def on_browse_iso(self):
        iso_path = self.frontend.select_file(
            _("Select an Ubuntu installation ISO image"))
        if not iso_path:
            return
        distro = self.application.backend.select_iso(iso_path)
        if not distro:
            self.frontend.show_error_message(
                _("The selected file is not a supported installation image:\n%s")
                % iso_path)
            return
        # Lock the selection to the distro the chosen ISO provides.
        self.info.distro = distro
        self.distro_list.clear()
        self.distro_list.add_item(distro.name)
        self.distro_list.set_value(distro.name)
        self.on_distro_change()
        self.frontend.show_info_message(
            _("Wubi will install from the selected ISO:\n%s") % iso_path)

    def on_mode_change(self, *_):
        '''Show/hide size picker depending on whether Wubi loop-file mode is selected.'''
        wubi = self.mode_wubi.is_checked()
        if wubi:
            self.size_picture.show()
            self.size_label_widget.show()
            self.size_list.show()
        else:
            self.size_picture.hide()
            self.size_label_widget.hide()
            self.size_list.hide()

    def on_drive_change(self):
        self.info.target_drive = self.get_drive()
        self.populate_size_list()

    def on_size_change(self):
        self.info.installation_size_mb = self.get_installation_size_mb()

    def on_cancel(self):
        self.frontend.cancel()

    def on_accessibility(self):
        self.frontend.show_page(self.frontend.accessibility_page)

    def _get_install_mode(self):
        if self.mode_autoinstall.is_checked():
            return 'autoinstall'
        if self.mode_guided.is_checked():
            return 'guided'
        return 'wubi'

    def check_real_partition_preconditions(self):
        '''
        Run the Windows-side pre-flight safety checks for the real-partition
        install modes. Show a blocking error for ``error`` findings and ask for
        confirmation on ``warning`` findings. Return True when it is safe to
        proceed, False when the install should be aborted.
        '''
        try:
            findings = self.application.backend.get_real_partition_findings()
        except Exception as err:
            log.exception("Could not evaluate real-partition preconditions: %s" % err)
            return True
        for severity, code, ctx in findings:
            if code == 'insufficient_space':
                free_gb = ctx.get('free_mb', 0) / 1024.0
                required_gb = ctx.get('required_mb', 0) / 1024.0
                message = _(
                    "Not enough free space to install Ubuntu on a real "
                    "partition alongside Windows.\n\n"
                    "About %(required).1fGB of free space is needed on your "
                    "Windows drive, but only %(free).1fGB is free.\n\n"
                    "Free up space in Windows (empty the Recycle Bin, remove "
                    "unused programs, run Disk Cleanup) and try again.") % dict(
                        required=required_gb, free=free_gb)
            elif code == 'volume_dirty':
                message = _(
                    "Your Windows drive is marked \"dirty\" and needs to be "
                    "checked before it can be safely resized.\n\n"
                    "Open an elevated command prompt and run 'chkdsk /F', then "
                    "reboot Windows once, before installing on a real "
                    "partition.\n\nDo you want to continue anyway?")
            elif code == 'fast_startup':
                message = _(
                    "Windows Fast Startup (hybrid shutdown) is enabled. This "
                    "leaves the Windows partition in a locked state that can "
                    "prevent resizing and risk data loss during a real-partition "
                    "install.\n\n"
                    "Disable it in Control Panel > Power Options > 'Choose what "
                    "the power buttons do' > uncheck 'Turn on fast startup', "
                    "then fully shut down Windows once.\n\n"
                    "Do you want to continue anyway?")
            elif code == 'bitlocker':
                message = _(
                    "BitLocker is enabled on: %s.\n\nResizing a BitLocker "
                    "protected drive can trigger a recovery prompt or data loss. "
                    "Suspend or disable BitLocker first.\n\n"
                    "Do you want to continue anyway?") % ", ".join(ctx.get('drives', []))
            else:
                message = _("A pre-installation check (%s) did not pass. "
                            "Do you want to continue anyway?") % code

            if severity == 'error':
                log.error("Real-partition precondition failed: %s %s" % (code, ctx))
                if self.info.non_interactive:
                    self.frontend.quit()
                else:
                    self.frontend.show_error_message(message)
                return False
            else:
                if self.info.non_interactive:
                    log.warning("Real-partition warning (%s): %s" % (code, message.replace("\n", " ")))
                elif not self.frontend.ask_confirmation(message):
                    log.info("User cancelled after real-partition warning: %s" % code)
                    return False
        return True

    def on_install(self):
        drive = self.get_drive()
        install_mode = self._get_install_mode()
        if install_mode == 'wubi':
            installation_size_mb = self.get_installation_size_mb()
        else:
            installation_size_mb = 0
        language = self.language_list.get_text()
        language = language2lang_country.get(language, None)
        locale = lang_country2linux_locale.get(language, self.info.locale)
        username = self.username.get_text()
        password1 = self.password1.get_text()
        password2 = self.password2.get_text()
        error_message = ""
        if not username:
            error_message = _("Please enter a valid username.")
        elif username != username.lower():
            error_message = _("Please use all lower cases in the username.")
        elif " " in username:
            error_message =  _("Please do not use spaces in the username.")
        elif not re_username_first.match(username):
            error_message =  _("Your username must start with a lower-case letter.")
        elif not re_username.match(username):
            error_message =  _("Your username must contain only lower-case letters, numbers, hyphens, and underscores.")
        elif username in reserved_usernames:
            error_message = _("The selected username is reserved, please select a different one.")
        elif not password1:
            error_message = _("Please enter a valid password.")
        elif " " in password1:
            error_message = _("Please do not use spaces in the password.")
        elif password1 != password2:
            error_message = _("Passwords do not match.")
        self.error_label.set_text(error_message)
        if error_message:
            if self.info.non_interactive:
                log.error("ERROR: %s Exiting." % error_message)
                self.frontend.quit()
            return
        log.debug(
            "install_mode=%s, target_drive=%s, installation_size=%sMB, distro_name=%s, language=%s, locale=%s, username=%s" \
            % (install_mode, drive.path, installation_size_mb, self.info.distro.name, language, locale, username))
        self.info.target_drive = drive
        self.info.installation_size_mb = installation_size_mb
        self.info.language = language
        self.info.locale = locale
        self.info.username = username
        self.info.password = password1
        # Warn if BitLocker protection is on for the target or system drive.
        bitlocker = getattr(self.info, 'bitlocker_drives', None) or set()
        affected = []
        for d in (drive, self.info.system_drive):
            if d and d.path and d.path[0].upper() in bitlocker and d.path not in affected:
                affected.append(d.path)
        if affected:
            message = _(
                "BitLocker drive encryption is enabled on: %s.\n\n"
                "Installing here changes the Windows boot configuration and "
                "may trigger a BitLocker recovery prompt on the next reboot. "
                "Make sure you have your BitLocker recovery key, or suspend "
                "BitLocker protection before continuing (in an elevated "
                "command prompt run: manage-bde -protectors -disable <drive>).\n\n"
                "Do you want to continue anyway?") % ", ".join(affected)
            if self.info.non_interactive:
                log.warning(message.replace("\n", " "))
            elif not self.frontend.ask_confirmation(message):
                log.info("User cancelled installation after BitLocker warning")
                return
        # Real-partition modes: run Windows-side pre-flight safety checks.
        # Errors block the install; warnings require confirmation.
        if install_mode in ('autoinstall', 'guided'):
            self.info.install_mode = install_mode
            if not self.check_real_partition_preconditions():
                return
        # Real-partition modes repartition the disk — warn before committing.
        if install_mode in ('autoinstall', 'guided'):
            if install_mode == 'autoinstall':
                mode_desc = _(
                    "Automated install alongside Windows will reboot your "
                    "computer into the Ubuntu installer which will automatically "
                    "resize your Windows partition and install Ubuntu on a real "
                    "partition.\n\n")
            else:
                mode_desc = _(
                    "Guided dual-boot mode will reboot your computer into the "
                    "Ubuntu live installer so you can partition the disk and "
                    "install Ubuntu on a real partition alongside Windows.\n\n")
            message = mode_desc + _(
                "Repartitioning can result in DATA LOSS if interrupted. "
                "Back up important files and close other programs before "
                "continuing.\n\nDo you want to continue?")
            if self.info.non_interactive:
                log.warning(message.replace("\n", " "))
            elif not self.frontend.ask_confirmation(message):
                log.info("User cancelled real-partition installation after repartition warning")
                return
        self.info.install_mode = install_mode
        log.debug("install_mode=%s" % install_mode)
        self.frontend.stop()

