"""History window for macOS: a translucent sidebar of dictations beside a reading pane.

Type to search, move with the arrow keys, and press Return (or double-click,
or use a row's copy button) to put a dictation back on the clipboard. New
dictations appear while the window is open.
"""

from __future__ import annotations

import logging

import AppKit
import objc
from Foundation import NSObject

from ...history import HistoryDB
from ..history_model import Entry, HistoryModel
from . import kit

log = logging.getLogger(__name__)

_SIZE = (900, 600)
_SIDEBAR_WIDTH = 330
_ENTRY_HEIGHT = 50
_DAY_HEIGHT = 24
_POLL_SECONDS = 2.0
_COPIED_SECONDS = 1.5
_TEXT_INSET = 28


def uses_24_hour_clock() -> bool:
    template = AppKit.NSDateFormatter.dateFormatFromTemplate_options_locale_("j", 0, AppKit.NSLocale.currentLocale())
    return "a" not in (template or "")


class DayCell(kit.RowCell):
    @objc.python_method
    def build(self) -> DayCell:
        title = kit.label("", 11, AppKit.NSFontWeightSemibold, AppKit.NSColor.secondaryLabelColor())
        self.addSubview_(title)
        self.setTextField_(title)
        kit.constrain(
            title.leadingAnchor().constraintEqualToAnchor_constant_(self.leadingAnchor(), 6),
            title.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.trailingAnchor(), -6),
            title.bottomAnchor().constraintEqualToAnchor_constant_(self.bottomAnchor(), -5),
        )
        return self


class EntryCell(kit.RowCell):
    @objc.python_method
    def build(self, target: NSObject) -> EntryCell:
        secondary = AppKit.NSColor.secondaryLabelColor()
        self.icon = kit.image_view(tint=secondary)
        self.text = kit.label("", 13)
        self.meta = kit.label("", 11, color=secondary)
        self.accessory = kit.icon_button("doc.on.doc", target, "copyRow:", "Copy")
        self.accessory.setHidden_(True)
        self.tinted = [(self.icon, secondary), (self.text, AppKit.NSColor.labelColor()), (self.meta, secondary),
                       (self.accessory, secondary)]
        for view in (self.icon, self.text, self.meta, self.accessory):
            self.addSubview_(view)
        kit.constrain(
            self.icon.leadingAnchor().constraintEqualToAnchor_constant_(self.leadingAnchor(), 4),
            self.icon.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()),
            self.icon.widthAnchor().constraintEqualToConstant_(20),
            self.text.leadingAnchor().constraintEqualToAnchor_constant_(self.icon.trailingAnchor(), 8),
            self.text.topAnchor().constraintEqualToAnchor_constant_(self.topAnchor(), 8),
            self.text.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.accessory.leadingAnchor(), -6),
            self.meta.leadingAnchor().constraintEqualToAnchor_(self.text.leadingAnchor()),
            self.meta.topAnchor().constraintEqualToAnchor_constant_(self.text.bottomAnchor(), 2),
            self.meta.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.accessory.leadingAnchor(), -6),
            self.accessory.trailingAnchor().constraintEqualToAnchor_constant_(self.trailingAnchor(), -4),
            self.accessory.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()),
            self.accessory.widthAnchor().constraintEqualToConstant_(22),
        )
        return self

    @objc.python_method
    def show(self, entry: Entry) -> None:
        self.text.setStringValue_(entry.preview)
        self.meta.setStringValue_(entry.meta)
        name = kit.APP_SYMBOLS.get(entry.record.app_type or "", kit.DEFAULT_APP_SYMBOL)
        self.icon.setImage_(kit.symbol(name, 13))
        self.accessory.setImage_(kit.symbol("doc.on.doc"))


class HistoryController(NSObject):
    def initWithModel_(self, model: HistoryModel) -> HistoryController:
        self = objc.super(HistoryController, self).init()
        if self is None:
            return None
        self.model = model
        self._flash_token = 0
        self._build()
        kit.attach(self.table, self)
        self.refresh()
        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _POLL_SECONDS, self, "poll:", None, True
        )
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown, self.handle_key
        )
        return self

    # -- Layout -----------------------------------------------------------

    @objc.python_method
    def _build(self) -> None:
        self.window = kit.window("History", _SIZE, (640, 400), full_size=True)
        self.window.setDelegate_(self)
        split = AppKit.NSSplitViewController.alloc().init()
        sidebar = AppKit.NSSplitViewItem.sidebarWithViewController_(kit.controller(self._build_sidebar()))
        sidebar.setMinimumThickness_(260)
        sidebar.setMaximumThickness_(460)
        sidebar.setCanCollapse_(False)
        split.addSplitViewItem_(sidebar)
        split.addSplitViewItem_(AppKit.NSSplitViewItem.splitViewItemWithViewController_(kit.controller(self._build_detail())))
        self.window.setContentViewController_(split)
        self.window.setContentSize_(_SIZE)

        toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("VoxHistory")
        toolbar.setDelegate_(self)
        toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconOnly)
        self.window.setToolbar_(toolbar)
        self.window.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
        # Let the reading pane's paper color run up under the title, like Notes
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)

    @objc.python_method
    def _build_sidebar(self) -> AppKit.NSView:
        view = kit.container(_SIDEBAR_WIDTH, _SIZE[1])
        self.search = AppKit.NSSearchField.alloc().init()
        self.search.setTarget_(self)
        self.search.setAction_("searchChanged:")
        self.search.setSendsSearchStringImmediately_(False)
        self.search.setControlSize_(AppKit.NSControlSizeLarge)
        self.search.setTranslatesAutoresizingMaskIntoConstraints_(False)

        scroll, self.table = kit.table(AppKit.NSTableViewStyleSourceList)
        self.table.setFloatsGroupRows_(True)
        self.table.setDoubleAction_("copySelected:")

        view.addSubview_(self.search)
        view.addSubview_(scroll)
        safe = view.safeAreaLayoutGuide()
        kit.constrain(
            self.search.topAnchor().constraintEqualToAnchor_constant_(safe.topAnchor(), 6),
            self.search.leadingAnchor().constraintEqualToAnchor_constant_(view.leadingAnchor(), 12),
            view.trailingAnchor().constraintEqualToAnchor_constant_(self.search.trailingAnchor(), 12),
            scroll.topAnchor().constraintEqualToAnchor_constant_(self.search.bottomAnchor(), 8),
            scroll.leadingAnchor().constraintEqualToAnchor_(view.leadingAnchor()),
            scroll.trailingAnchor().constraintEqualToAnchor_(view.trailingAnchor()),
            scroll.bottomAnchor().constraintEqualToAnchor_(view.bottomAnchor()),
        )
        return view

    @objc.python_method
    def _build_detail(self) -> AppKit.NSView:
        view = kit.PaperView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, _SIZE[0] - _SIDEBAR_WIDTH, _SIZE[1]))
        secondary = AppKit.NSColor.secondaryLabelColor()

        self.stamp = kit.label("", 15, AppKit.NSFontWeightSemibold)
        self.app_icon = kit.image_view(tint=secondary)
        self.details = kit.label("", 12, color=secondary)
        details_row = kit.stack([self.app_icon, self.details], vertical=False, spacing=5)
        titles = kit.stack([self.stamp, details_row], spacing=3)

        self.copy_button = AppKit.NSButton.buttonWithTitle_image_target_action_(
            "Copy", kit.symbol("doc.on.doc", 12), self, "copySelected:"
        )
        self.copy_button.setImagePosition_(AppKit.NSImageLeading)
        self.copy_button.setControlSize_(AppKit.NSControlSizeLarge)
        self.copy_button.setKeyEquivalent_("\r")  # the accent-colored default button; Return triggers it
        self.copy_button.setToolTip_("Copy to the clipboard (Return)")
        self.copy_button.setTranslatesAutoresizingMaskIntoConstraints_(False)

        self.text_scroll = AppKit.NSTextView.scrollableTextView()
        self.text_scroll.setDrawsBackground_(False)
        self.text_scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.text_view = self.text_scroll.documentView()
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setDrawsBackground_(False)
        self.text_view.setTextContainerInset_((_TEXT_INSET, 4))
        self.text_view.textContainer().setLineFragmentPadding_(0)

        self.content = AppKit.NSView.alloc().init()
        for sub in (titles, self.copy_button, self.text_scroll):
            self.content.addSubview_(sub)
        kit.constrain(
            titles.topAnchor().constraintEqualToAnchor_constant_(self.content.topAnchor(), 18),
            titles.leadingAnchor().constraintEqualToAnchor_constant_(self.content.leadingAnchor(), _TEXT_INSET),
            titles.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(self.copy_button.leadingAnchor(), -16),
            self.copy_button.centerYAnchor().constraintEqualToAnchor_(titles.centerYAnchor()),
            self.content.trailingAnchor().constraintEqualToAnchor_constant_(self.copy_button.trailingAnchor(), _TEXT_INSET - 4),
            self.text_scroll.topAnchor().constraintEqualToAnchor_constant_(titles.bottomAnchor(), 18),
            self.text_scroll.leadingAnchor().constraintEqualToAnchor_(self.content.leadingAnchor()),
            self.text_scroll.trailingAnchor().constraintEqualToAnchor_(self.content.trailingAnchor()),
            self.text_scroll.bottomAnchor().constraintEqualToAnchor_constant_(self.content.bottomAnchor(), -12),
        )

        self.empty, self.empty_title, self.empty_message = kit.empty_state("waveform")
        view.addSubview_(self.content)
        view.addSubview_(self.empty)
        kit.pin(self.content, view.safeAreaLayoutGuide())
        kit.center(self.empty, view.safeAreaLayoutGuide())
        return view

    # -- Toolbar: only the sidebar separator, so the title sits over the reading pane

    def toolbarDefaultItemIdentifiers_(self, toolbar) -> list:
        return [AppKit.NSToolbarSidebarTrackingSeparatorItemIdentifier]

    def toolbarAllowedItemIdentifiers_(self, toolbar) -> list:
        return self.toolbarDefaultItemIdentifiers_(toolbar)

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, insert):
        return None

    # -- Table ------------------------------------------------------------

    def numberOfRowsInTableView_(self, table) -> int:
        return len(self.model.rows)

    def tableView_isGroupRow_(self, table, row: int) -> bool:
        return isinstance(self.model.rows[row], str)

    def tableView_shouldSelectRow_(self, table, row: int) -> bool:
        return isinstance(self.model.rows[row], Entry)

    def tableView_heightOfRow_(self, table, row: int) -> float:
        return _DAY_HEIGHT if isinstance(self.model.rows[row], str) else _ENTRY_HEIGHT

    def tableView_rowViewForRow_(self, table, row: int):
        if isinstance(self.model.rows[row], str):
            return None
        return kit.HoverRowView.alloc().initWithFrame_(AppKit.NSZeroRect)

    def tableView_viewForTableColumn_row_(self, table, column, row: int):
        item = self.model.rows[row]
        if isinstance(item, str):
            cell = kit.reuse(table, "day", lambda: DayCell.alloc().initWithFrame_(AppKit.NSZeroRect).build())
            cell.textField().setStringValue_(item)
            return cell
        cell = kit.reuse(table, "entry", lambda: EntryCell.alloc().initWithFrame_(AppKit.NSZeroRect).build(self))
        cell.show(item)
        return cell

    def tableViewSelectionDidChange_(self, notification) -> None:
        self._show_selected()

    # -- Actions ----------------------------------------------------------

    def searchChanged_(self, sender) -> None:
        self.refresh()

    def copySelected_(self, sender) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._copy(entry, self.copy_button)

    def copyRow_(self, sender) -> None:
        row = self.table.rowForView_(sender)
        if 0 <= row < len(self.model.rows) and isinstance(self.model.rows[row], Entry):
            self.table.selectRowIndexes_byExtendingSelection_(AppKit.NSIndexSet.indexSetWithIndex_(row), False)
            self._copy(self.model.rows[row], sender)

    def poll_(self, timer) -> None:
        entry = self.selected_entry()
        if self.model.refresh_if_changed():
            self._render(entry.record.id if entry else None)

    def windowWillClose_(self, notification) -> None:
        self._timer.invalidate()
        AppKit.NSEvent.removeMonitor_(self._monitor)

    # -- State ------------------------------------------------------------

    @objc.python_method
    def refresh(self) -> None:
        """Search for what's in the search field and select the newest match."""
        self.model.search(self.search.stringValue())
        self._render(None)

    @objc.python_method
    def _render(self, keep_id: int | None) -> None:
        self.search.setPlaceholderString_(self.model.placeholder)
        self.table.reloadData()
        rows = self.model.rows
        entries = [i for i, row in enumerate(rows) if isinstance(row, Entry)]
        keep = [i for i in entries if rows[i].record.id == keep_id]
        target = (keep or entries or [None])[0]
        if target is None:
            self.table.deselectAll_(None)
        else:
            self.table.selectRowIndexes_byExtendingSelection_(AppKit.NSIndexSet.indexSetWithIndex_(target), False)
            self.table.scrollRowToVisible_(target)
        self._show_selected()

    @objc.python_method
    def selected_entry(self) -> Entry | None:
        row = self.table.selectedRow()
        rows = self.model.rows
        return rows[row] if 0 <= row < len(rows) and isinstance(rows[row], Entry) else None

    @objc.python_method
    def _show_selected(self) -> None:
        entry = self.selected_entry()
        self.content.setHidden_(entry is None)
        self.empty.setHidden_(entry is not None)
        if entry is None:
            title, message = self.model.empty_state() or ("No Selection", "Choose a dictation to read it here.")
            icon = "magnifyingglass" if self.model.query.strip() else "waveform"
            self.empty.views()[0].setImage_(kit.symbol(icon, 34, AppKit.NSFontWeightLight))
            self.empty_title.setStringValue_(title)
            self.empty_message.setStringValue_(message)
            return
        self.stamp.setStringValue_(entry.stamp)
        self.details.setStringValue_(entry.details or "Dictation")
        name = kit.APP_SYMBOLS.get(entry.record.app_type or "", kit.DEFAULT_APP_SYMBOL)
        self.app_icon.setImage_(kit.symbol(name, 11))
        self._reset_copy_button()

        paragraph = AppKit.NSMutableParagraphStyle.alloc().init()
        paragraph.setLineHeightMultiple_(1.25)
        paragraph.setParagraphSpacing_(2)
        attributes = {
            AppKit.NSFontAttributeName: kit.font(15),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(),
            AppKit.NSParagraphStyleAttributeName: paragraph,
        }
        text = AppKit.NSAttributedString.alloc().initWithString_attributes_(entry.text, attributes)
        self.text_view.textStorage().setAttributedString_(text)
        self.text_view.scrollRangeToVisible_((0, 0))

    @objc.python_method
    def move_selection(self, step: int) -> None:
        rows = self.model.rows
        row = self.table.selectedRow()
        candidates = range(row + step, len(rows)) if step > 0 else range(row + step, -1, -1)
        target = next((i for i in candidates if i >= 0 and isinstance(rows[i], Entry)), None)
        if target is not None:
            self.table.selectRowIndexes_byExtendingSelection_(AppKit.NSIndexSet.indexSetWithIndex_(target), False)
            self.table.scrollRowToVisible_(target)

    @objc.python_method
    def _copy(self, entry: Entry, button: AppKit.NSButton) -> None:
        error = self.model.copy(entry)
        if error:
            kit.alert(self.window, "Couldn’t Copy", error)
            return
        self._flash_token += 1
        token = self._flash_token
        button.setImage_(kit.symbol("checkmark", 12, AppKit.NSFontWeightSemibold))
        if button is self.copy_button:
            button.setTitle_("Copied")

        def restore() -> None:
            if token == self._flash_token:
                self._reset_copy_button()
                if button is not self.copy_button:
                    button.setImage_(kit.symbol("doc.on.doc"))

        kit.later(_COPIED_SECONDS, restore)

    @objc.python_method
    def _reset_copy_button(self) -> None:
        self.copy_button.setTitle_("Copy")
        self.copy_button.setImage_(kit.symbol("doc.on.doc", 12))

    # -- Keyboard ---------------------------------------------------------

    @objc.python_method
    def handle_key(self, event: AppKit.NSEvent) -> AppKit.NSEvent | None:
        """Spotlight-style keys: arrows move through results while typing, Return copies, Escape clears then closes."""
        if event.window() != self.window or self.window.attachedSheet() is not None:
            return event
        responder = self.window.firstResponder()
        if isinstance(responder, AppKit.NSTextView) and responder.hasMarkedText():
            return event  # an input method is composing text
        code = event.keyCode()
        flags = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        plain = not flags & ~(AppKit.NSEventModifierFlagFunction | AppKit.NSEventModifierFlagNumericPad)
        command = flags == AppKit.NSEventModifierFlagCommand
        in_search = self.search.currentEditor() is not None and responder == self.search.currentEditor()
        key = (event.charactersIgnoringModifiers() or "").lower()

        if code == kit.KEY_ESCAPE and plain:
            if self.search.stringValue():
                self.search.setStringValue_("")
                self.refresh()
            else:
                self.window.performClose_(None)
            return None
        if code in (kit.KEY_RETURN, kit.KEY_ENTER) and plain:
            self.copySelected_(None)
            return None
        if in_search and code in (kit.KEY_DOWN, kit.KEY_UP) and plain:
            self.move_selection(1 if code == kit.KEY_DOWN else -1)
            return None
        if command and key == "c":
            if isinstance(responder, AppKit.NSTextView) and responder.selectedRange().length > 0:
                return event  # copy the selected words, not the whole dictation
            self.copySelected_(None)
            return None
        if command and key == "f":
            self.window.makeFirstResponder_(self.search)
            return None
        typed = event.characters() or ""
        if responder == self.table and not flags & ~AppKit.NSEventModifierFlagShift and typed.isprintable() and typed:
            # Typing in the list searches, like Finder
            self.window.makeFirstResponder_(self.search)
            editor = self.search.currentEditor()
            editor.setSelectedRange_((len(self.search.stringValue()), 0))
        return event


def run(db: HistoryDB) -> None:
    kit.application()
    controller = HistoryController.alloc().initWithModel_(HistoryModel(db, clock24=uses_24_hour_clock()))
    kit.run(controller.window, controller.search)
