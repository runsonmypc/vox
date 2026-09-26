"""Vocabulary window for macOS: settings-style Vocabulary and Snippets tabs.

Words are added from a field at the top and removed from their row; snippets
open in a sheet to add, edit or delete. Every change is saved to config.toml
at once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject

from ..vocab_model import VocabModel
from . import kit

log = logging.getLogger(__name__)

_SIZE = (580, 540)
_PAD = 20
_WORD_HEIGHT = 32
_SNIPPET_HEIGHT = 48
_NOTE_SECONDS = 3.0

_WORDS_INTRO = "Names, jargon and acronyms Vox should always spell exactly as written."
_SNIPPETS_INTRO = "Say a trigger phrase on its own and Vox types the expansion instead."


class WordCell(kit.RowCell):
    @objc.python_method
    def build(self, target: NSObject) -> WordCell:
        self.text = kit.label("", 13)
        self.accessory = kit.icon_button("xmark.circle.fill", target, "removeWord:", "Remove")
        self.accessory.setContentTintColor_(AppKit.NSColor.tertiaryLabelColor())
        self.accessory.setHidden_(True)
        self.tinted = [(self.text, AppKit.NSColor.labelColor()), (self.accessory, AppKit.NSColor.tertiaryLabelColor())]
        self.addSubview_(self.text)
        self.addSubview_(self.accessory)
        kit.constrain(
            self.text.leadingAnchor().constraintEqualToAnchor_constant_(self.leadingAnchor(), 6),
            self.text.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()),
            self.text.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.accessory.leadingAnchor(), -6),
            self.accessory.trailingAnchor().constraintEqualToAnchor_constant_(self.trailingAnchor(), -4),
            self.accessory.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()),
        )
        return self


class SnippetCell(kit.RowCell):
    @objc.python_method
    def build(self, target: NSObject) -> SnippetCell:
        secondary = AppKit.NSColor.secondaryLabelColor()
        self.trigger = kit.label("", 13, AppKit.NSFontWeightMedium)
        self.expansion = kit.label("", 12, color=secondary)
        self.accessory = kit.icon_button("xmark.circle.fill", target, "removeSnippet:", "Delete")
        self.accessory.setContentTintColor_(AppKit.NSColor.tertiaryLabelColor())
        self.accessory.setHidden_(True)
        self.tinted = [(self.trigger, AppKit.NSColor.labelColor()), (self.expansion, secondary),
                       (self.accessory, AppKit.NSColor.tertiaryLabelColor())]
        for view in (self.trigger, self.expansion, self.accessory):
            self.addSubview_(view)
        kit.constrain(
            self.trigger.leadingAnchor().constraintEqualToAnchor_constant_(self.leadingAnchor(), 6),
            self.trigger.topAnchor().constraintEqualToAnchor_constant_(self.topAnchor(), 7),
            self.trigger.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.accessory.leadingAnchor(), -6),
            self.expansion.leadingAnchor().constraintEqualToAnchor_(self.trigger.leadingAnchor()),
            self.expansion.topAnchor().constraintEqualToAnchor_constant_(self.trigger.bottomAnchor(), 2),
            self.expansion.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.accessory.leadingAnchor(), -6),
            self.accessory.trailingAnchor().constraintEqualToAnchor_constant_(self.trailingAnchor(), -4),
            self.accessory.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()),
        )
        return self


class VocabController(NSObject):
    def initWithModel_(self, model: VocabModel) -> VocabController:
        self = objc.super(VocabController, self).init()
        if self is None:
            return None
        self.model = model
        self.editor = None
        self._note_token = 0
        self._build()
        kit.attach(self.words_table, self)
        kit.attach(self.snippets_table, self)
        model.reload()
        self.render()
        if model.load_error:
            self.show_error("Couldn’t Read Your Settings", model.load_error)
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown, self.handle_key
        )
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        self.window = kit.window("Vocabulary", _SIZE, (460, 380))
        self.window.setDelegate_(self)
        self.tabs = AppKit.NSTabViewController.alloc().init()
        self.tabs.setTabStyle_(AppKit.NSTabViewControllerTabStyleToolbar)
        for title, symbol, view in (
            ("Vocabulary", "character.book.closed", self._build_words()),
            ("Snippets", "text.insert", self._build_snippets()),
        ):
            vc = kit.controller(view)
            vc.setTitle_(title)
            item = AppKit.NSTabViewItem.tabViewItemWithViewController_(vc)
            item.setLabel_(title)
            item.setImage_(AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title))
            self.tabs.addTabViewItem_(item)
        self.window.setContentViewController_(self.tabs)
        self.window.setToolbarStyle_(AppKit.NSWindowToolbarStylePreference)
        self.window.setContentSize_(_SIZE)

    @objc.python_method
    def _pane(self, intro: str, accessory: AppKit.NSView, table_style: int) -> tuple:
        """Intro line with an accessory beside or under it, a boxed list, and a footer."""
        view = kit.container(*_SIZE)
        text = kit.label(intro, 13, color=AppKit.NSColor.secondaryLabelColor(), wrap=True)
        box = kit.rounded_box()
        scroll, table = kit.table(table_style)
        box.contentView().addSubview_(scroll)
        kit.pin(scroll, box.contentView(), top=1, bottom=1)
        footer = kit.label("", 11, color=AppKit.NSColor.tertiaryLabelColor())
        for sub in (text, accessory, box, footer):
            view.addSubview_(sub)
        kit.constrain(
            text.topAnchor().constraintEqualToAnchor_constant_(view.topAnchor(), _PAD),
            text.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
            box.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
            view.trailingAnchor().constraintEqualToAnchor_constant_(box.trailingAnchor(), _PAD),
            footer.topAnchor().constraintEqualToAnchor_constant_(box.bottomAnchor(), 10),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(box.leadingAnchor(), 2),
            footer.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(box.trailingAnchor(), 0),
            view.bottomAnchor().constraintEqualToAnchor_constant_(footer.bottomAnchor(), 16),
        )
        empty, title, message = kit.empty_state("character.book.closed")
        box.contentView().addSubview_(empty)
        kit.center(empty, box.contentView())
        return view, text, box, table, footer, (empty, title, message)

    @objc.python_method
    def _build_words(self) -> AppKit.NSView:
        self.word_field = kit.text_field("Add a word, or several separated by commas")
        self.word_field.setTarget_(self)
        self.word_field.setAction_("addWord:")
        self.word_field.cell().setSendsActionOnEndEditing_(False)
        self.add_button = AppKit.NSButton.buttonWithTitle_target_action_("Add", self, "addWord:")
        self.add_button.setControlSize_(AppKit.NSControlSizeLarge)
        adder = kit.stack([self.word_field, self.add_button], vertical=False, spacing=8)

        view, text, box, self.words_table, self.words_footer, empty = self._pane(
            _WORDS_INTRO, adder, AppKit.NSTableViewStyleInset
        )
        self.words_table.setAllowsMultipleSelection_(True)
        self.words_empty = empty
        empty[1].setStringValue_("No Words Yet")
        empty[2].setStringValue_("Add names and terms Vox tends to get wrong.")
        kit.constrain(
            view.trailingAnchor().constraintEqualToAnchor_constant_(text.trailingAnchor(), _PAD),
            adder.topAnchor().constraintEqualToAnchor_constant_(text.bottomAnchor(), 12),
            adder.leadingAnchor().constraintEqualToAnchor_(box.leadingAnchor()),
            adder.trailingAnchor().constraintEqualToAnchor_(box.trailingAnchor()),
            box.topAnchor().constraintEqualToAnchor_constant_(adder.bottomAnchor(), 12),
        )
        return view

    @objc.python_method
    def _build_snippets(self) -> AppKit.NSView:
        new = AppKit.NSButton.buttonWithTitle_image_target_action_(
            "New Snippet", kit.symbol("plus", 11, AppKit.NSFontWeightSemibold), self, "newSnippet:"
        )
        new.setImagePosition_(AppKit.NSImageLeading)
        new.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view, text, box, self.snippets_table, self.snippets_footer, empty = self._pane(
            _SNIPPETS_INTRO, new, AppKit.NSTableViewStyleInset
        )
        self.snippets_table.setDoubleAction_("editSnippet:")
        self.snippets_empty = empty
        empty[0].views()[0].setImage_(kit.symbol("text.insert", 34, AppKit.NSFontWeightLight))
        empty[1].setStringValue_("No Snippets Yet")
        empty[2].setStringValue_("Type an address, a sign-off or a link just by saying a short phrase.")
        kit.constrain(
            new.centerYAnchor().constraintEqualToAnchor_(text.centerYAnchor()),
            view.trailingAnchor().constraintEqualToAnchor_constant_(new.trailingAnchor(), _PAD),
            text.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(new.leadingAnchor(), -16),
            box.topAnchor().constraintEqualToAnchor_constant_(text.bottomAnchor(), 14),
        )
        return view

    # -- Tables -----------------------------------------------------------

    def numberOfRowsInTableView_(self, table) -> int:
        return len(self.model.words) if table == self.words_table else len(self.model.snippets)

    def tableView_heightOfRow_(self, table, row: int) -> float:
        return _WORD_HEIGHT if table == self.words_table else _SNIPPET_HEIGHT

    def tableView_rowViewForRow_(self, table, row: int):
        return kit.HoverRowView.alloc().initWithFrame_(AppKit.NSZeroRect)

    def tableView_viewForTableColumn_row_(self, table, column, row: int):
        if table == self.words_table:
            cell = kit.reuse(table, "word", lambda: WordCell.alloc().initWithFrame_(AppKit.NSZeroRect).build(self))
            cell.text.setStringValue_(self.model.words[row])
            return cell
        trigger, expansion = list(self.model.snippets.items())[row]
        cell = kit.reuse(table, "snippet", lambda: SnippetCell.alloc().initWithFrame_(AppKit.NSZeroRect).build(self))
        cell.trigger.setStringValue_(trigger)
        cell.expansion.setStringValue_(" ".join(expansion.split()))
        return cell

    # -- Actions ----------------------------------------------------------

    def addWord_(self, sender) -> None:
        text = self.word_field.stringValue()
        if not text.strip():
            return
        added: list[str] = []
        if not self.change(lambda: added.extend(self.model.add_words(text))):
            return
        self.word_field.setStringValue_("")
        self.window.makeFirstResponder_(self.word_field)
        rows = [i for i, w in enumerate(self.model.words) if w in added]
        if rows:
            self._select(self.words_table, rows)
            self.note(f"Added {_quoted(added)}.")
        else:
            self.note("Already in your vocabulary.")

    def removeWord_(self, sender) -> None:
        row = self.words_table.rowForView_(sender)
        if 0 <= row < len(self.model.words):
            self.remove_words([self.model.words[row]])

    def newSnippet_(self, sender) -> None:
        self.open_editor(None)

    def editSnippet_(self, sender) -> None:
        row = self.snippets_table.clickedRow() if sender == self.snippets_table else self.snippets_table.selectedRow()
        if 0 <= row < len(self.model.snippets):
            self.open_editor(list(self.model.snippets)[row])

    def removeSnippet_(self, sender) -> None:
        row = self.snippets_table.rowForView_(sender)
        if 0 <= row < len(self.model.snippets):
            self.confirm_remove_snippet(list(self.model.snippets)[row])

    def windowWillClose_(self, notification) -> None:
        AppKit.NSEvent.removeMonitor_(self._monitor)

    # -- Model ------------------------------------------------------------

    @objc.python_method
    def change(self, write: Callable[[], object]) -> bool:
        """Run a config write and redraw. Returns False after showing the error."""
        try:
            write()
        except Exception as e:
            log.warning("Config update failed: %s", e)
            self.show_error("Couldn’t Save", str(e))
            return False
        self.render()
        return True

    @objc.python_method
    def remove_words(self, words: list[str]) -> None:
        if self.change(lambda: self.model.remove_words(words)):
            # The table keeps the selected row numbers, which now point at the words that moved up
            self.words_table.deselectAll_(None)
            self.note(f"Removed {_quoted(words)}.")

    @objc.python_method
    def save_snippet(self, trigger: str, expansion: str, original: str | None) -> bool:
        if not self.change(lambda: self.model.save_snippet(trigger, expansion, original)):
            return False
        keys = list(self.model.snippets)
        row = next((i for i, t in enumerate(keys) if t == trigger.strip()), None)
        if row is not None:
            self._select(self.snippets_table, [row])
        self.note(f"Saved “{trigger.strip()}”.")
        return True

    @objc.python_method
    def remove_snippet(self, trigger: str) -> None:
        if self.change(lambda: self.model.remove_snippet(trigger)):
            self.note(f"Deleted “{trigger}”.")

    @objc.python_method
    def confirm_remove_snippet(self, trigger: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"Delete “{trigger}”?")
        alert.setInformativeText_("Vox will stop expanding this phrase.")
        alert.addButtonWithTitle_("Delete").setHasDestructiveAction_(True)
        alert.addButtonWithTitle_("Cancel")

        def done(response: int) -> None:
            if response == AppKit.NSAlertFirstButtonReturn:
                self.remove_snippet(trigger)

        alert.beginSheetModalForWindow_completionHandler_(self.window, done)

    # -- Rendering --------------------------------------------------------

    @objc.python_method
    def render(self) -> None:
        for table, items, empty in (
            (self.words_table, self.model.words, self.words_empty),
            (self.snippets_table, self.model.snippets, self.snippets_empty),
        ):
            table.reloadData()
            table.enclosingScrollView().setHidden_(not items)
            empty[0].setHidden_(bool(items))
        writable = self.model.load_error is None
        for control in (self.word_field, self.add_button):
            control.setEnabled_(writable)
        self._set_footer(None)

    @objc.python_method
    def note(self, message: str) -> None:
        """Say what just happened in the footer for a moment, then go back to where changes are saved."""
        self._note_token += 1
        token = self._note_token
        self._set_footer(f"{message} Vox picks this up within a few seconds.")
        kit.later(_NOTE_SECONDS, lambda: token == self._note_token and self._set_footer(None))

    @objc.python_method
    def _set_footer(self, message: str | None) -> None:
        if self.model.load_error:
            text, color = f"Couldn’t read {self.model.shown_path}", AppKit.NSColor.systemRedColor()
        else:
            text, color = message or f"Saved to {self.model.shown_path}", AppKit.NSColor.tertiaryLabelColor()
        for footer in (self.words_footer, self.snippets_footer):
            footer.setStringValue_(text)
            footer.setTextColor_(color)

    @objc.python_method
    def show_error(self, title: str, message: str) -> None:
        kit.alert(self.editor.window if self.editor else self.window, title, message)

    @objc.python_method
    def _select(self, table: AppKit.NSTableView, rows: list[int]) -> None:
        indexes = AppKit.NSMutableIndexSet.indexSet()
        for row in rows:
            indexes.addIndex_(row)
        table.selectRowIndexes_byExtendingSelection_(indexes, False)
        table.scrollRowToVisible_(rows[-1])

    @objc.python_method
    def open_editor(self, original: str | None) -> None:
        if self.editor is not None:
            return
        self.tabs.setSelectedTabViewItemIndex_(1)
        self.editor = SnippetEditor.alloc().initWithOwner_original_(self, original)
        self.window.beginSheet_completionHandler_(self.editor.window, None)
        self.editor.window.makeFirstResponder_(self.editor.trigger if original is None else self.editor.expansion)

    @objc.python_method
    def close_editor(self) -> None:
        if self.editor is not None:
            self.window.endSheet_(self.editor.window)
            self.editor = None

    # -- Keyboard ---------------------------------------------------------

    @objc.python_method
    def handle_key(self, event: AppKit.NSEvent) -> AppKit.NSEvent | None:
        code = event.keyCode()
        flags = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        if self.editor is not None and event.window() == self.editor.window:
            if code in (kit.KEY_RETURN, kit.KEY_ENTER) and flags == AppKit.NSEventModifierFlagCommand:
                self.editor.save_(None)
                return None
            return event
        if event.window() != self.window:
            return event
        responder = self.window.firstResponder()
        if isinstance(responder, AppKit.NSTextView) and responder.hasMarkedText():
            return event  # an input method is composing text
        plain = not flags & ~(AppKit.NSEventModifierFlagFunction | AppKit.NSEventModifierFlagNumericPad)
        if code == kit.KEY_ESCAPE and plain:
            self.window.performClose_(None)
            return None
        if flags == AppKit.NSEventModifierFlagCommand and kit.shortcut_key(event) == "n":
            self.open_editor(None)
            return None
        if code in (kit.KEY_DELETE, kit.KEY_FORWARD_DELETE) and plain:
            if event.isARepeat() and responder in (self.words_table, self.snippets_table):
                return None  # a held key removes what was selected, not the rows that move up after it
            if responder == self.words_table:
                rows = self.words_table.selectedRowIndexes()
                words = [w for i, w in enumerate(self.model.words) if rows.containsIndex_(i)]
                if words:
                    self.remove_words(words)
                return None
            if responder == self.snippets_table and self.snippets_table.selectedRow() >= 0:
                self.confirm_remove_snippet(list(self.model.snippets)[self.snippets_table.selectedRow()])
                return None
        if code in (kit.KEY_RETURN, kit.KEY_ENTER) and plain and responder == self.snippets_table:
            self.editSnippet_(None)
            return None
        return event


class SnippetEditor(NSObject):
    """Sheet for adding or editing one snippet."""

    def initWithOwner_original_(self, owner: VocabController, original: str | None) -> SnippetEditor:
        self = objc.super(SnippetEditor, self).init()
        if self is None:
            return None
        self.owner = owner
        self.original = original
        self._build()
        if original is not None:
            self.trigger.setStringValue_(original)
            self.expansion.setString_(owner.model.snippets.get(original, ""))
        self.validate()
        return self

    @objc.python_method
    def _build(self) -> None:
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 480, 320), AppKit.NSWindowStyleMaskTitled, AppKit.NSBackingStoreBuffered, False
        )
        self.window.setReleasedWhenClosed_(False)
        view = self.window.contentView()

        heading = kit.label("New Snippet" if self.original is None else "Edit Snippet", 15, AppKit.NSFontWeightSemibold)
        self.trigger = kit.text_field("my email")
        self.trigger.setDelegate_(self)

        scroll = AppKit.NSTextView.scrollableTextView()
        scroll.setDrawsBackground_(False)
        field_box = kit.rounded_box(radius=6)
        field_box.setFillColor_(AppKit.NSColor.textBackgroundColor())
        field_box.contentView().addSubview_(scroll)
        kit.pin(scroll, field_box.contentView(), top=1, leading=1, bottom=1, trailing=1)
        self.expansion = scroll.documentView()
        self.expansion.setFont_(kit.font(13))
        self.expansion.setRichText_(False)
        self.expansion.setAllowsUndo_(True)
        self.expansion.setDrawsBackground_(False)
        self.expansion.setTextContainerInset_((4, 6))
        # Snippets are pasted verbatim, so keep quotes, dashes and spelling exactly as typed
        for setter in ("setAutomaticQuoteSubstitutionEnabled_", "setAutomaticDashSubstitutionEnabled_",
                       "setAutomaticTextReplacementEnabled_", "setAutomaticSpellingCorrectionEnabled_"):
            getattr(self.expansion, setter)(False)
        self.expansion.setDelegate_(self)

        secondary = AppKit.NSColor.secondaryLabelColor()
        grid = AppKit.NSGridView.gridViewWithViews_([
            [kit.label("When you say", 13, color=secondary), self.trigger],
            [kit.label("Vox types", 13, color=secondary), field_box],
        ])
        grid.setRowSpacing_(12)
        grid.setColumnSpacing_(12)
        grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
        grid.rowAtIndex_(0).setYPlacement_(AppKit.NSGridCellPlacementCenter)
        grid.rowAtIndex_(1).setYPlacement_(AppKit.NSGridCellPlacementTop)
        grid.setTranslatesAutoresizingMaskIntoConstraints_(False)

        self.warning = kit.label("", 11, color=AppKit.NSColor.systemOrangeColor())
        self.save_button = AppKit.NSButton.buttonWithTitle_target_action_("Save", self, "save:")
        self.save_button.setKeyEquivalent_("\r")  # the default button; ⌘Return saves from the expansion too
        self.save_button.setToolTip_("Save (⌘Return)")
        cancel = AppKit.NSButton.buttonWithTitle_target_action_("Cancel", self, "cancel:")
        cancel.setKeyEquivalent_("\x1b")
        buttons = [cancel, self.save_button]
        if self.original is not None:
            delete = AppKit.NSButton.buttonWithTitle_target_action_("Delete", self, "delete:")
            delete.setHasDestructiveAction_(True)
            view.addSubview_(delete)
            delete.setTranslatesAutoresizingMaskIntoConstraints_(False)
            kit.constrain(
                delete.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
                view.bottomAnchor().constraintEqualToAnchor_constant_(delete.bottomAnchor(), _PAD),
            )
        row = kit.stack(buttons, vertical=False, spacing=10)

        for sub in (heading, grid, self.warning, row):
            view.addSubview_(sub)
        kit.constrain(
            heading.topAnchor().constraintEqualToAnchor_constant_(view.topAnchor(), _PAD),
            heading.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
            grid.topAnchor().constraintEqualToAnchor_constant_(heading.bottomAnchor(), 16),
            grid.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), _PAD),
            view.trailingAnchor().constraintEqualToAnchor_constant_(grid.trailingAnchor(), _PAD),
            field_box.heightAnchor().constraintEqualToConstant_(130),
            self.warning.topAnchor().constraintEqualToAnchor_constant_(grid.bottomAnchor(), 8),
            self.warning.leadingAnchor().constraintEqualToAnchor_(self.trigger.leadingAnchor()),
            self.warning.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(view.trailingAnchor(), -_PAD),
            row.topAnchor().constraintEqualToAnchor_constant_(self.warning.bottomAnchor(), 14),
            view.trailingAnchor().constraintEqualToAnchor_constant_(row.trailingAnchor(), _PAD),
            view.bottomAnchor().constraintEqualToAnchor_constant_(row.bottomAnchor(), _PAD),
        )

    # -- Delegates and actions ---------------------------------------------

    def controlTextDidChange_(self, notification) -> None:
        self.validate()

    def control_textView_doCommandBySelector_(self, control, text_view, selector) -> bool:
        if selector != "insertNewline:":
            return False
        # Return in the trigger moves on to an empty expansion, and saves otherwise
        if self.expansion.string().strip():
            self.save_(None)
        else:
            self.window.makeFirstResponder_(self.expansion)
        return True

    def textDidChange_(self, notification) -> None:
        self.validate()

    def save_(self, sender) -> None:
        if self.save_button.isEnabled() and self.owner.save_snippet(
            self.trigger.stringValue(), self.expansion.string(), self.original
        ):
            self.owner.close_editor()

    def cancel_(self, sender) -> None:
        self.owner.close_editor()

    def delete_(self, sender) -> None:
        self.owner.close_editor()
        self.owner.remove_snippet(self.original)

    @objc.python_method
    def validate(self) -> None:
        trigger = self.trigger.stringValue()
        self.save_button.setEnabled_(bool(trigger.strip() and self.expansion.string().strip()))
        clash = self.owner.model.conflict(trigger, self.original)
        self.warning.setStringValue_(f"This replaces your “{clash}” snippet." if clash else "")


def _quoted(words: list[str]) -> str:
    if len(words) == 1:
        return f"“{words[0]}”"
    return f"{len(words)} words"


def run(model: VocabModel) -> None:
    kit.application()
    controller = VocabController.alloc().initWithModel_(model)
    kit.run(controller.window, controller.word_field)
