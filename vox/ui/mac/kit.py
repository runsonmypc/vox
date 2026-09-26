"""Small AppKit helpers shared by the macOS windows."""

from __future__ import annotations

import logging
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject
from PyObjCTools import AppHelper

log = logging.getLogger(__name__)

APP_SYMBOLS = {
    "TERMINAL": "terminal",
    "EDITOR": "chevron.left.forwardslash.chevron.right",
    "CHAT": "bubble.left",
    "EMAIL": "envelope",
    "BROWSER": "globe",
}
DEFAULT_APP_SYMBOL = "waveform"

KEY_RETURN, KEY_ENTER, KEY_ESCAPE, KEY_DELETE, KEY_FORWARD_DELETE = 36, 76, 53, 51, 117
KEY_DOWN, KEY_UP = 125, 126


# -- Views --------------------------------------------------------------------


def symbol(name: str, size: float = 13, weight: float = AppKit.NSFontWeightRegular) -> AppKit.NSImage:
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)
    return image.imageWithSymbolConfiguration_(config)


def font(size: float, weight: float = AppKit.NSFontWeightRegular) -> AppKit.NSFont:
    return AppKit.NSFont.systemFontOfSize_weight_(size, weight)


def label(
    text: str = "",
    size: float = 13,
    weight: float = AppKit.NSFontWeightRegular,
    color: AppKit.NSColor | None = None,
    wrap: bool = False,
) -> AppKit.NSTextField:
    field = AppKit.NSTextField.wrappingLabelWithString_(text) if wrap else AppKit.NSTextField.labelWithString_(text)
    field.setFont_(font(size, weight))
    field.setTextColor_(color or AppKit.NSColor.labelColor())
    if not wrap:
        field.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        field.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return field


def icon_button(symbol_name: str, target: NSObject, action: str, tooltip: str, size: float = 13) -> AppKit.NSButton:
    """A borderless symbol button, quiet until the row it sits in is hovered or selected."""
    button = AppKit.NSButton.buttonWithImage_target_action_(symbol(symbol_name, size), target, action)
    button.setBordered_(False)
    button.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
    button.setToolTip_(tooltip)
    button.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return button


def image_view(image: AppKit.NSImage | None = None, tint: AppKit.NSColor | None = None) -> AppKit.NSImageView:
    view = AppKit.NSImageView.imageViewWithImage_(image) if image else AppKit.NSImageView.alloc().init()
    view.setContentTintColor_(tint or AppKit.NSColor.secondaryLabelColor())
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return view


def stack(views: list, vertical: bool = True, spacing: float = 8, align_leading: bool = True) -> AppKit.NSStackView:
    view = AppKit.NSStackView.stackViewWithViews_(views)
    view.setOrientation_(
        AppKit.NSUserInterfaceLayoutOrientationVertical if vertical else AppKit.NSUserInterfaceLayoutOrientationHorizontal
    )
    view.setSpacing_(spacing)
    if vertical:
        view.setAlignment_(AppKit.NSLayoutAttributeLeading if align_leading else AppKit.NSLayoutAttributeCenterX)
    else:
        view.setAlignment_(AppKit.NSLayoutAttributeCenterY)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return view


def empty_state(symbol_name: str) -> tuple[AppKit.NSStackView, AppKit.NSTextField, AppKit.NSTextField]:
    """A centered symbol, title and message, for lists with nothing to show."""
    title = label("", 15, AppKit.NSFontWeightSemibold, AppKit.NSColor.secondaryLabelColor())
    message = label("", 12, color=AppKit.NSColor.tertiaryLabelColor(), wrap=True)
    message.setAlignment_(AppKit.NSTextAlignmentCenter)
    message.setPreferredMaxLayoutWidth_(260)
    icon = image_view(symbol(symbol_name, 34, AppKit.NSFontWeightLight), AppKit.NSColor.tertiaryLabelColor())
    view = stack([icon, title, message], spacing=6, align_leading=False)
    view.setCustomSpacing_afterView_(12, icon)
    return view, title, message


def text_field(placeholder: str) -> AppKit.NSTextField:
    """A large, rounded single-line field that lines up with large buttons."""
    field = AppKit.NSTextField.textFieldWithString_("")
    field.setPlaceholderString_(placeholder)
    field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
    field.setControlSize_(AppKit.NSControlSizeLarge)
    field.setFont_(font(13))
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return field


def rounded_box(radius: float = 10) -> AppKit.NSBox:
    """The grouped, hairline-bordered container System Settings uses for lists."""
    box = AppKit.NSBox.alloc().init()
    box.setBoxType_(AppKit.NSBoxCustom)
    box.setCornerRadius_(radius)
    box.setBorderWidth_(1)
    box.setBorderColor_(AppKit.NSColor.separatorColor())
    box.setFillColor_(AppKit.NSColor.controlBackgroundColor())
    box.setContentViewMargins_((0, 0))
    box.setTitlePosition_(AppKit.NSNoTitle)
    box.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return box


def table(style: int) -> tuple[AppKit.NSScrollView, AppKit.NSTableView]:
    """A single-column, header-less view-based table in a transparent scroll view. Connect it with ``attach``."""
    view = AppKit.NSTableView.alloc().initWithFrame_(AppKit.NSZeroRect)
    column = AppKit.NSTableColumn.alloc().initWithIdentifier_("main")
    column.setResizingMask_(AppKit.NSTableColumnAutoresizingMask)
    view.addTableColumn_(column)
    view.setHeaderView_(None)
    view.setStyle_(style)
    view.setColumnAutoresizingStyle_(AppKit.NSTableViewFirstColumnOnlyAutoresizingStyle)
    view.setBackgroundColor_(AppKit.NSColor.clearColor())
    view.setIntercellSpacing_((0, 0))
    scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSZeroRect)
    scroll.setDocumentView_(view)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setDrawsBackground_(False)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return scroll, view


def attach(table_view: AppKit.NSTableView, target: NSObject) -> None:
    """Make ``target`` the table's data source, delegate and action target, once it can answer for it."""
    table_view.setDataSource_(target)
    table_view.setDelegate_(target)
    table_view.setTarget_(target)


# -- Layout -------------------------------------------------------------------


def constrain(*constraints) -> None:
    AppKit.NSLayoutConstraint.activateConstraints_(list(constraints))


def pin(view, to, top: float = 0, leading: float = 0, bottom: float = 0, trailing: float = 0) -> None:
    """Pin all four edges of ``view`` inside ``to`` (a view or layout guide)."""
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    constrain(
        view.topAnchor().constraintEqualToAnchor_constant_(to.topAnchor(), top),
        view.leadingAnchor().constraintEqualToAnchor_constant_(to.leadingAnchor(), leading),
        to.bottomAnchor().constraintEqualToAnchor_constant_(view.bottomAnchor(), bottom),
        to.trailingAnchor().constraintEqualToAnchor_constant_(view.trailingAnchor(), trailing),
    )


def center(view, within) -> None:
    constrain(
        view.centerXAnchor().constraintEqualToAnchor_(within.centerXAnchor()),
        view.centerYAnchor().constraintEqualToAnchor_(within.centerYAnchor()),
    )


def container(width: float, height: float) -> AppKit.NSView:
    return AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, height))


class PaperView(AppKit.NSView):
    """A view filled with the text background color: white in light mode, near-black in dark."""

    def drawRect_(self, rect) -> None:
        AppKit.NSColor.textBackgroundColor().setFill()
        AppKit.NSRectFill(rect)


def controller(view: AppKit.NSView) -> AppKit.NSViewController:
    """A view controller for a view built in code (a bare NSViewController would look for a nib)."""
    vc = AppKit.NSViewController.alloc().init()
    vc.setView_(view)
    return vc


# -- Rows ---------------------------------------------------------------------


class HoverRowView(AppKit.NSTableRowView):
    """Row that reveals its cell's accessory button while hovered or selected."""

    def updateTrackingAreas(self) -> None:
        objc.super(HoverRowView, self).updateTrackingAreas()
        if not self.trackingAreas():
            options = (
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp
                | AppKit.NSTrackingInVisibleRect
            )
            self.addTrackingArea_(
                AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(AppKit.NSZeroRect, options, self, None)
            )

    def mouseEntered_(self, event) -> None:
        self.hovered = True
        self.sync()

    def mouseExited_(self, event) -> None:
        self.hovered = False
        self.sync()

    def setSelected_(self, selected: bool) -> None:
        objc.super(HoverRowView, self).setSelected_(selected)
        self.sync()

    def didAddSubview_(self, subview) -> None:
        objc.super(HoverRowView, self).didAddSubview_(subview)
        self.sync()

    @objc.python_method
    def sync(self) -> None:
        show = self.isSelected() or getattr(self, "hovered", False)
        for i in range(self.numberOfColumns()):
            cell = self.viewAtColumn_(i)
            accessory = getattr(cell, "accessory", None)
            if accessory is not None:
                accessory.setHidden_(not show)


class RowCell(AppKit.NSTableCellView):
    """Cell whose labels switch to the selection's text color on an emphasized (accent) highlight."""

    def setBackgroundStyle_(self, style: int) -> None:
        objc.super(RowCell, self).setBackgroundStyle_(style)
        emphasized = style == AppKit.NSBackgroundStyleEmphasized
        for view, color in getattr(self, "tinted", ()):
            tint = AppKit.NSColor.alternateSelectedControlTextColor() if emphasized else color
            if isinstance(view, AppKit.NSTextField):
                view.setTextColor_(tint)
            else:
                view.setContentTintColor_(tint)


def reuse(table_view: AppKit.NSTableView, identifier: str, make: Callable[[], AppKit.NSView]) -> AppKit.NSView:
    view = table_view.makeViewWithIdentifier_owner_(identifier, None)
    if view is None:
        view = make()
        view.setIdentifier_(identifier)
    return view


# -- App ----------------------------------------------------------------------


def window(title: str, size: tuple[float, float], min_size: tuple[float, float], full_size: bool = False) -> AppKit.NSWindow:
    style = (
        AppKit.NSWindowStyleMaskTitled
        | AppKit.NSWindowStyleMaskClosable
        | AppKit.NSWindowStyleMaskMiniaturizable
        | AppKit.NSWindowStyleMaskResizable
    )
    if full_size:
        style |= AppKit.NSWindowStyleMaskFullSizeContentView
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, *size), style, AppKit.NSBackingStoreBuffered, False
    )
    win.setTitle_(title)
    win.setReleasedWhenClosed_(False)
    win.setContentMinSize_(min_size)
    return win


def alert(window: AppKit.NSWindow, title: str, message: str) -> None:
    sheet = AppKit.NSAlert.alloc().init()
    sheet.setAlertStyle_(AppKit.NSAlertStyleWarning)
    sheet.setMessageText_(title)
    sheet.setInformativeText_(message)
    sheet.beginSheetModalForWindow_completionHandler_(window, None)


def later(seconds: float, fn: Callable[[], None]) -> None:
    AppHelper.callLater(seconds, fn)


class _AppDelegate(NSObject):
    def applicationDidBecomeActive_(self, notification) -> None:
        # Opening the window again from the menu bar activates this process; bring a minimized window back too
        for win in AppKit.NSApplication.sharedApplication().windows():
            if win.isMiniaturized():
                win.deminiaturize_(None)

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:
        return True


_delegate = None


def application() -> AppKit.NSApplication:
    """The shared app: no Dock icon (Vox lives in the menu bar), but a main menu so ⌘C, ⌘V, ⌘W and ⌘Q work."""
    global _delegate
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    if _delegate is None:
        _delegate = _AppDelegate.alloc().init()
        app.setDelegate_(_delegate)
        app.setMainMenu_(_main_menu())
    return app


def _main_menu() -> AppKit.NSMenu:
    bar = AppKit.NSMenu.alloc().init()
    for title, items in (
        ("Vox", [("Close Window", "performClose:", "w"), ("Quit Vox", "terminate:", "q")]),
        ("Edit", [
            ("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), None,
            ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a"),
        ]),
    ):
        menu = AppKit.NSMenu.alloc().initWithTitle_(title)
        for item in items:
            if item is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
            else:
                menu.addItemWithTitle_action_keyEquivalent_(*item)
        holder = AppKit.NSMenuItem.alloc().init()
        holder.setSubmenu_(menu)
        bar.addItem_(holder)
    return bar


def run(win: AppKit.NSWindow, first_responder: AppKit.NSView | None = None) -> None:
    """Show ``win`` in front of other apps and run until it closes."""
    app = application()
    win.center()
    win.makeKeyAndOrderFront_(None)
    if first_responder is not None:
        win.makeFirstResponder_(first_responder)
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop(unexpectedErrorAlert=lambda: True)
