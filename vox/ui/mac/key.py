"""API key window for macOS: a secure field, where the key is kept, and Save, Remove and Cancel.

The key is typed into an NSSecureTextField and saved straight to the Keychain.
"Check with OpenAI" lists models on a background thread before saving.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject
from PyObjCTools import AppHelper

from ...keystore import KeystoreError
from ..key_model import CHECK_BY_DEFAULT, KEYS_URL, CheckResult, KeyModel, Outcome, check_key
from . import kit

log = logging.getLogger(__name__)

_WIDTH = 460
_PAD = 20
_TEXT_WIDTH = _WIDTH - 2 * _PAD

_INTRO = "Vox sends your dictation to OpenAI to transcribe it, using your own API key."


def _in_background(work: Callable[[], object], done: Callable[[object], None]) -> None:
    threading.Thread(target=lambda: AppHelper.callAfter(done, work()), daemon=True).start()


def _confirm(window: AppKit.NSWindow, title: str, message: str, button: str, destructive: bool,
             then: Callable[[], None]) -> None:
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_(button).setHasDestructiveAction_(destructive)
    alert.addButtonWithTitle_("Cancel")

    def done(response: int) -> None:
        if response == AppKit.NSAlertFirstButtonReturn:
            then()

    alert.beginSheetModalForWindow_completionHandler_(window, done)


def _key_field(secure: bool) -> AppKit.NSTextField:
    field = (AppKit.NSSecureTextField if secure else AppKit.NSTextField).alloc().init()
    field.setPlaceholderString_("sk-…")
    field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
    field.setControlSize_(AppKit.NSControlSizeLarge)
    field.setFont_(kit.font(13))
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return field


class KeyController(NSObject):
    def initWithModel_(self, model: KeyModel) -> KeyController:
        self = objc.super(KeyController, self).init()
        if self is None:
            return None
        self.model = model
        self.closed = False
        self.background = _in_background  # replaced in tests
        self.confirm = _confirm
        self._build()
        self.render()
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, _WIDTH, 300),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("OpenAI API Key")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        view = self.window.contentView()
        secondary = AppKit.NSColor.secondaryLabelColor()

        heading = kit.label("OpenAI API Key", 15, AppKit.NSFontWeightSemibold)
        intro = self._wrapping(_INTRO, 13, secondary)
        link = AppKit.NSButton.buttonWithTitle_target_action_("Get a key from OpenAI…", self, "openKeysPage:")
        link.setBezelStyle_(AppKit.NSBezelStyleInline)
        link.setBordered_(False)
        link.setContentTintColor_(AppKit.NSColor.linkColor())
        link.setTranslatesAutoresizingMaskIntoConstraints_(False)

        # A secure field, and a plain one swapped in while Show is ticked
        self.secure_field = _key_field(secure=True)
        self.plain_field = _key_field(secure=False)
        self.plain_field.setHidden_(True)
        fields = AppKit.NSView.alloc().init()
        fields.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for field in (self.secure_field, self.plain_field):
            fields.addSubview_(field)
            kit.pin(field, fields)
        self.show_box = AppKit.NSButton.checkboxWithTitle_target_action_("Show key", self, "toggleShow:")

        self.saved = kit.label("", 12, color=secondary)
        self.storage_icon = kit.image_view()
        self.storage = self._wrapping("", 12, secondary)
        storage_row = kit.stack([self.storage_icon, self.storage], vertical=False, spacing=6)
        self.override = self._wrapping("", 12, AppKit.NSColor.systemOrangeColor())
        self.check_box = AppKit.NSButton.checkboxWithTitle_target_action_("Check with OpenAI before saving", None, None)
        self.check_box.setState_(AppKit.NSControlStateValueOn if CHECK_BY_DEFAULT else AppKit.NSControlStateValueOff)
        self.status = self._wrapping("", 12, secondary)

        self.save_button = AppKit.NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        self.save_button.setKeyEquivalent_("\r")
        cancel = AppKit.NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        cancel.setKeyEquivalent_("\x1b")
        self.remove_button = AppKit.NSButton.buttonWithTitle_target_action_("Remove Key…", self, "remove:")
        self.remove_button.setHasDestructiveAction_(True)
        spacer = AppKit.NSView.alloc().init()
        spacer.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
        buttons = kit.stack([self.remove_button, spacer, cancel, self.save_button], vertical=False, spacing=10)

        content = kit.stack([heading, intro, link, fields, self.show_box, self.saved, storage_row, self.override,
                             self.check_box, self.status, buttons], spacing=8)
        content.setCustomSpacing_afterView_(4, heading)
        content.setCustomSpacing_afterView_(14, link)
        content.setCustomSpacing_afterView_(12, self.show_box)
        content.setCustomSpacing_afterView_(12, self.override)
        content.setCustomSpacing_afterView_(16, self.check_box)  # used when the status line is hidden
        content.setCustomSpacing_afterView_(16, self.status)
        view.addSubview_(content)
        kit.pin(content, view, top=_PAD, leading=_PAD, bottom=_PAD, trailing=_PAD)
        kit.constrain(
            fields.widthAnchor().constraintEqualToConstant_(_TEXT_WIDTH),
            buttons.widthAnchor().constraintEqualToConstant_(_TEXT_WIDTH),
        )

    @objc.python_method
    def _wrapping(self, text: str, size: float, color: AppKit.NSColor) -> AppKit.NSTextField:
        field = kit.label(text, size, color=color, wrap=True)
        field.setPreferredMaxLayoutWidth_(_TEXT_WIDTH)
        return field

    # -- Rendering --------------------------------------------------------

    @objc.python_method
    def render(self) -> None:
        model = self.model
        self.saved.setStringValue_(model.saved_text)
        self.saved.setTextColor_(
            AppKit.NSColor.systemRedColor() if model.read_error else AppKit.NSColor.secondaryLabelColor()
        )
        self.storage.setStringValue_(model.storage_text)
        if model.encrypted:
            self.storage_icon.setImage_(kit.symbol("lock.fill", 11))
            self.storage_icon.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
        else:
            self.storage_icon.setImage_(kit.symbol("exclamationmark.triangle.fill", 11))
            self.storage_icon.setContentTintColor_(AppKit.NSColor.systemOrangeColor())
        override = model.override_text
        self.override.setStringValue_(override or "")
        self.override.setHidden_(override is None)
        self.remove_button.setHidden_(not model.has_saved_key)
        self.set_status("")

    @objc.python_method
    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setStringValue_(text)
        self.status.setHidden_(not text)  # so an empty line leaves no gap above the buttons
        self.status.setTextColor_(AppKit.NSColor.systemRedColor() if error else AppKit.NSColor.secondaryLabelColor())

    @objc.python_method
    def set_busy(self, busy: bool) -> None:
        for control in (self.save_button, self.remove_button, self.secure_field, self.plain_field, self.check_box):
            control.setEnabled_(not busy)

    @objc.python_method
    def field(self) -> AppKit.NSTextField:
        return self.plain_field if self.show_box.state() == AppKit.NSControlStateValueOn else self.secure_field

    # -- Actions ----------------------------------------------------------

    def toggleShow_(self, sender) -> None:
        showing = self.show_box.state() == AppKit.NSControlStateValueOn
        source, target = (self.secure_field, self.plain_field) if showing else (self.plain_field, self.secure_field)
        target.setStringValue_(source.stringValue())
        source.setHidden_(True)
        target.setHidden_(False)
        self.window.makeFirstResponder_(target)

    def openKeysPage_(self, sender) -> None:
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(KEYS_URL))

    def save_(self, sender) -> None:
        key = self.field().stringValue()
        problem = KeyModel.problem(key)
        if problem is not None:
            self.set_status(problem, error=True)
            return
        if self.check_box.state() != AppKit.NSControlStateValueOn:
            self.store(key)
            return
        self.set_busy(True)
        self.set_status("Checking with OpenAI…")
        self.background(lambda: check_key(key), lambda result: self.checked(key, result))

    def cancel_(self, sender) -> None:
        self.finish()

    def remove_(self, sender) -> None:
        self.confirm(
            self.window, "Remove the Saved Key?",
            "Vox can’t transcribe with OpenAI until you save a key again.", "Remove", True, self.remove_key,
        )

    def windowWillClose_(self, notification) -> None:
        self.closed = True

    # -- Saving -----------------------------------------------------------

    @objc.python_method
    def checked(self, key: str, result: CheckResult) -> None:
        self.set_busy(False)
        if result.outcome is Outcome.ACCEPTED:
            self.store(key)
        elif result.outcome is Outcome.REJECTED:
            self.set_status(result.message, error=True)
        else:
            self.set_status("")
            self.confirm(self.window, "Couldn’t Check the Key", f"{result.message} Save it anyway?",
                         "Save Anyway", False, lambda: self.store(key))

    @objc.python_method
    def store(self, key: str) -> None:
        try:
            self.model.save(key)
        except (KeystoreError, OSError) as e:
            log.warning("Couldn't save the API key: %s", e)
            kit.alert(self.window, "Couldn’t Save the Key",
                      f"{e}\n\nIf macOS asked whether Vox may use the Keychain, choose Always Allow and try again.")
            return
        log.info("Saved the OpenAI API key")
        self.finish()

    @objc.python_method
    def remove_key(self) -> None:
        try:
            self.model.remove()
        except (KeystoreError, OSError) as e:
            kit.alert(self.window, "Couldn’t Remove the Key", str(e))
            return
        log.info("Removed the OpenAI API key")
        self.finish()

    @objc.python_method
    def finish(self) -> None:
        self.closed = True
        self.window.close()


def run(model: KeyModel) -> None:
    kit.application()
    controller = KeyController.alloc().initWithModel_(model)
    kit.run(controller.window, controller.secure_field)
