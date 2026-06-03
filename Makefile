export SHELL = sh
PACKAGE = wubi
ICON = data/images/Wubi.ico
VERSION = $(shell head -n 1 debian/changelog | sed -e "s/^$(PACKAGE) (\(.*\)).*/\1/g" | cut -d r -f 1)
REVISION = $(shell head -n 1 debian/changelog | sed -e "s/^$(PACKAGE) (\(.*\)).*/\1/g" | cut -d r -f 2)
COPYRIGHTYEAR = 2009
AUTHOR = Agostino Russo
EMAIL = agostino.russo@gmail.com

all: build check

build: wubi

wubi: wubi-pre-build
	tools/pywine -m PyInstaller --noconfirm --clean --log-level=WARN \
		--distpath build --workpath build/pyinstaller wubi.spec

wubizip: wubi-pre-build
	cp -a wine/drive_c/Python312 build/wubi/python
	cd build; zip -r wubi.zip wubi

# Stage the application sources (plus the generated version.py) into
# build/wubi/lib and the runtime resources into build/wubi/{data,bin,winboot,
# translations}. This staging tree is the input consumed by PyInstaller (see
# wubi.spec) and is also the layout exercised by the unit tests
# (tests/test_backend.py).
wubi-pre-build: check_wine check_winboot winboot2 src/main.py src/wubi/*.py cpuid version.py translations
	rm -rf build/wubi
	rm -rf build/bin
	cp -a blobs build/bin
	cp build/cpuid/cpuid.dll build/bin
	mkdir -p build/wubi/lib
	cp src/main.py build/wubi/lib/main.py
	cp build/version.py build/wubi/lib/version.py
	for pkg in wubi winui openpgp bittorrent urlgrabber; do cp -a src/$$pkg build/wubi/lib/; done
	cp -a data build/wubi/data
	cp -a build/bin build/wubi/bin
	cp -a build/winboot build/wubi/winboot
	cp -a build/translations build/wubi/translations

pot:
	xgettext --default-domain="$(PACKAGE)" --output="po/$(PACKAGE).pot" $(shell find src/wubi -name "*.py" | sort)
	sed -i 's/SOME DESCRIPTIVE TITLE/Translation template for $(PACKAGE)/' po/$(PACKAGE).pot
	sed -i "s/YEAR THE PACKAGE'S COPYRIGHT HOLDER/$(COPYRIGHTYEAR)/" po/$(PACKAGE).pot
	sed -i 's/FIRST AUTHOR <EMAIL@ADDRESS>, YEAR/$(AUTHOR) <$(EMAIL)>, $(COPYRIGHTYEAR)/' po/$(PACKAGE).pot
	sed -i 's/Report-Msgid-Bugs-To: /Report-Msgid-Bugs-To: $(EMAIL)/' po/$(PACKAGE).pot
	sed -i 's/CHARSET/UTF-8/' po/$(PACKAGE).pot
	sed -i 's/PACKAGE VERSION/$(VERSION)-r$(REVISION)/' po/$(PACKAGE).pot
	sed -i 's/PACKAGE/$(PACKAGE)/' po/$(PACKAGE).pot

update-po: pot
	for i in po/*.po ;\
	do \
	mv $$i $${i}.old ; \
	(msgmerge $${i}.old po/wubi.pot | msgattrib --no-obsolete > $$i) ; \
	rm $${i}.old ; \
	done

translations: po/*.po
	mkdir -p build/translations/
	@for po in $^; do \
		language=`basename $$po`; \
		language=$${language%%.po}; \
		target="build/translations/$$language/LC_MESSAGES"; \
		mkdir -p $$target; \
		msgfmt --output=$$target/$(PACKAGE).mo $$po; \
	done

version.py:
	mkdir -p build
	echo 'version = "$(VERSION)"' > build/version.py
	echo 'revision = $(REVISION)' >> build/version.py
	echo 'application_name = "$(PACKAGE)"' >> build/version.py

cpuid: src/cpuid/cpuid.c
	cp -rf src/cpuid build
	cd build/cpuid && make

winboot2:
	mkdir -p build/winboot
	cp -f data/wubildr.cfg data/wubildr-bootstrap.cfg build/winboot/
	/usr/lib/grub/i386-pc/grub-ntldr-img --grub2 --boot-file=wubildr -o build/winboot/wubildr.mbr
	cd build/winboot && tar cf wubildr.tar wubildr.cfg
	mkdir -p build/grubutil
	grub-mkimage -O i386-pc -c build/winboot/wubildr-bootstrap.cfg -m build/winboot/wubildr.tar -o build/grubutil/core.img \
		loadenv biosdisk part_msdos part_gpt fat ntfs ext2 ntfscomp iso9660 loopback search linux boot minicmd cat cpuid chain halt help ls reboot \
		echo test configfile gzio normal sleep memdisk tar font gfxterm gettext true vbe vga video_bochs video_cirrus probe
	cat /usr/lib/grub/i386-pc/lnxboot.img build/grubutil/core.img > build/winboot/wubildr
	mkdir -p build/winboot/EFI
	grub-mkimage -O x86_64-efi -c build/winboot/wubildr-bootstrap.cfg -m build/winboot/wubildr.tar -o build/winboot/EFI/grubx64.efi \
		loadenv part_msdos part_gpt fat ntfs ext2 ntfscomp iso9660 loopback search linux linuxefi boot minicmd cat cpuid chain halt help ls reboot \
		echo test configfile gzio normal sleep memdisk tar font gfxterm gettext true efi_gop efi_uga video_bochs video_cirrus probe efifwsetup \
		all_video gfxterm_background png gfxmenu
	cp /usr/lib/shim/shim.efi.signed build/winboot/EFI/shimx64.efi 2>/dev/null || \
		cp shim/shimx64.efi.signed build/winboot/EFI/shimx64.efi 2>/dev/null || \
		cp /usr/lib/shim/shimx64.efi.signed build/winboot/EFI/shimx64.efi
	cp /usr/lib/shim/MokManager.efi.signed build/winboot/EFI/MokManager.efi 2>/dev/null || \
		cp shim/mmx64.efi build/winboot/EFI/mmx64.efi 2>/dev/null || \
		cp /usr/lib/shim/mmx64.efi build/winboot/EFI/mmx64.efi
	sbsign --key .key/*.key --cert .key/*.crt --output build/winboot/EFI/grubx64.efi build/winboot/EFI/grubx64.efi
	grub-mkimage -O i386-efi -c build/winboot/wubildr-bootstrap.cfg -m build/winboot/wubildr.tar -o build/winboot/EFI/grubia32.efi \
		loadenv part_msdos part_gpt fat ntfs ext2 ntfscomp iso9660 loopback search linux linuxefi boot minicmd cat cpuid chain halt help ls reboot \
		echo test configfile gzio normal sleep memdisk tar font gfxterm gettext true efi_gop efi_uga video_bochs video_cirrus probe efifwsetup \
		all_video gfxterm_background png gfxmenu
	sbsign --key .key/*.key --cert .key/*.crt --output build/winboot/EFI/grubia32.efi build/winboot/EFI/grubia32.efi
	cp .key/*.cer build/winboot/EFI/.

winboot: grub4dos grubutil
	mkdir -p build/winboot
	cp -f data/menu.winboot build/winboot/menu.lst
	cp -f build/grub4dos/stage2/grldr build/winboot/wubildr
	cp -f build/grub4dos/stage2/grub.exe build/winboot/wubildr.exe
	dd if=build/winboot/wubildr of=build/winboot/wubildr.mbr bs=1 count=8192
	cd build/winboot; ../grubutil/grubinst/grubinst -o -b=wubildr wubildr.mbr

grub4dos: src/grub4dos/*
	cp -rf src/grub4dos build
	cd build/grub4dos;./configure --enable-preset-menu=../../data/menu.winboot
	cd build/grub4dos; make

grubutil: src/grubutil/grubinst/*
	cp -rf src/grubutil build
	cd build/grubutil/grubinst; make

runbin: wubi
	rm -rf build/test
	mkdir build/test
	cd build/test; ../../tools/wine ../wubi.exe --test

check_wine: tools/check_wine
	tools/check_wine

check_winboot: tools/check_winboot
	tools/check_winboot

unittest:
	tools/pywine tools/test

check: wubi
	tests/run

runpy:
	PYTHONPATH=src tools/pywine src/main.py --test

clean:
	rm -rf dist/*
	rm -rf build/*

distclean: clean
	rm -rf wine
	rm -rf tools/buildtest/*
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyc" -delete
	rm -rf .key
	rm -rf data/custom-installation/packages
	rm -rf shim

.PHONY: all build test wubi wubizip wubi-pre-build pot runpy runbin check_wine check_winboot unittest
	translations version.py winboot winboot2 grubutil grub4dos clean distclean
