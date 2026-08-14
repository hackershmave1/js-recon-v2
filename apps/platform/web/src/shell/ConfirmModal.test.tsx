import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmModal } from "./ConfirmModal";

describe("ConfirmModal", () => {
  it("fires onConfirm (not onCancel) when the confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmModal title="Delete session?" message="Gone for good" confirmLabel="Delete"
        danger onConfirm={onConfirm} onCancel={onCancel} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("cancels on the cancel button and on Escape", async () => {
    const onCancel = vi.fn();
    render(<ConfirmModal title="Cancel this run?" cancelLabel="Keep running"
      onConfirm={() => {}} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep running" }));
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("collects a trimmed value in prompt mode and blocks confirm while empty", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmModal title="Rename session" confirmLabel="Save"
        input={{ label: "Name", initialValue: "" }} onConfirm={onConfirm} onCancel={() => {}} />,
    );
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "  renamed  ");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(onConfirm).toHaveBeenCalledWith("renamed");
  });
});
