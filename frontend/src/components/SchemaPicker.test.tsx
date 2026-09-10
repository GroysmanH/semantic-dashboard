import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SchemaInfo } from "../api/client";
import SchemaPicker from "./SchemaPicker";

const CATALOGUE: SchemaInfo[] = [
  {
    schema: "ddh",
    answerable: 2,
    tables: [
      { table: "fct_production_daily", is_view: false, entity: "production",
        label: "Daily Production", unverified: [], joined_only: false },
      { table: "dim_wells", is_view: false, entity: null, label: "",
        unverified: [], joined_only: true },
      { table: "fct_field_targets_monthly", is_view: false, entity: null,
        label: "", unverified: [], joined_only: false },
      { table: "fct_well_interventions", is_view: false,
        entity: "well_interventions", label: "Well Interventions",
        unverified: ["dimension status"], joined_only: false },
    ],
  },
  {
    schema: "dm_planning",
    answerable: 1,
    tables: [
      { table: "field_targets_monthly", is_view: true, entity: "field_targets",
        label: "Field Targets", unverified: [], joined_only: false },
    ],
  },
  { schema: "stg", answerable: 0, tables: [
      { table: "wells_raw", is_view: false, entity: null, label: "",
        unverified: [], joined_only: false },
  ] },
];

function open() {
  return userEvent.setup();
}

describe("SchemaPicker", () => {
  it("names the schema the dashboard is currently on", () => {
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /ddh/ })).toBeVisible();
  });

  it("lists real tables and says why the unanswerable ones are unanswerable",
     async () => {
    const user = open();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));

    const panel = screen.getByRole("dialog", { name: "Choose a schema" });
    expect(within(panel).getByText("fct_production_daily")).toBeVisible();
    // Modelled, but only as part of something else.
    expect(within(panel).getByText(/joined into another entity/)).toBeVisible();
    // Really nothing, and the reason is the honest one.
    expect(within(panel).getByText(/nobody has defined what its columns mean/))
      .toBeVisible();
    // An entity behind the confidence gate says so rather than looking fine.
    expect(within(panel).getByText(/unverified: dimension status/)).toBeVisible();
  });

  it("lets you look inside a schema without switching to it", async () => {
    const user = open();
    const onChoose = vi.fn();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={onChoose} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));
    await user.click(screen.getByRole("button", { name: /dm_planning/ }));

    expect(screen.getByText("field_targets_monthly")).toBeVisible();
    // Browsing is not choosing. Nothing has changed yet.
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("says what switching will and will not do, before the click", async () => {
    const user = open();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));
    await user.click(screen.getByRole("button", { name: /dm_planning/ }));

    expect(screen.getByText(/Cards already here keep working/)).toBeVisible();
  });

  it("switches only when the switch is asked for", async () => {
    const user = open();
    const onChoose = vi.fn();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={onChoose} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));
    await user.click(screen.getByRole("button", { name: /dm_planning/ }));
    await user.click(screen.getByRole("button",
                                      { name: "Ask dm_planning on this dashboard" }));

    expect(onChoose).toHaveBeenCalledWith("dm_planning");
  });

  it("refuses a schema nothing is modelled in", async () => {
    const user = open();
    const onChoose = vi.fn();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={onChoose} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));
    await user.click(screen.getByRole("button", { name: /stg/ }));

    // Visible and browsable -- it is a real schema -- but a dashboard
    // pointed at it could ask nothing at all.
    expect(screen.getByText("wells_raw")).toBeVisible();
    expect(screen.getByRole("button", { name: "Nothing to ask here" }))
      .toBeDisabled();
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("closes on Escape without changing anything", async () => {
    const user = open();
    const onChoose = vi.fn();
    render(<SchemaPicker schemas={CATALOGUE} current="ddh" busy={false}
                         onChoose={onChoose} />);
    await user.click(screen.getByRole("button", { name: /ddh/ }));
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onChoose).not.toHaveBeenCalled();
  });
});
