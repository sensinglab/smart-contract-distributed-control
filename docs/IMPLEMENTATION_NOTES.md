# Implementation and provenance notes

## Preserved runtime source

The documentation and configuration examples were revised on 29 September 2026. Both Python Bridge files, both JavaScript rule files, Items, sitemaps, pinned dependencies, deployment freezes, and reference systemd units remain unchanged from the supplied source snapshot.

The prototype uses PyTeal contracts embedded in the Bridge source. Addresses accepted by state-changing methods are embedded when the contract is compiled. The source initializes four global keys: `state`, `active`, `last_actor`, and `updated_at`. Building ownership and peer-discovery information are also maintained in the local registry and lifecycle notes; they should not be described as additional stored global keys in this implementation.

## Synchronization and removal

The watcher polls node status, processes newly confirmed rounds sequentially, updates local files, and triggers openHAB. The openHAB rules synchronize on that trigger and startup, not on a periodic two-second timer.

Logical removal removes operational registry/UI entries and sends a payment note. It does not delete the application, change the on-chain `active` value to false, or revoke the existing on-chain sender authorisations. A party still authorised by the contract can call it outside the removed UI entry. Application deletion and explicit on-chain revocation are separate extensions.

Both Bridge processes expose the REST methods. The evaluated UI uses A for creation/removal and B for control; those roles are not separate HTTP authentication checks.

## Payment configuration and historical evidence

The input environment examples used `ANNOUNCE_AMOUNT_MICROALGO=1000`. This preparation changes **only the examples** to an explicit zero amount, matching the zero-value signalling setup described in the dissertation. Both runtime files retain the original fallback of 1000 when the setting is absent.

In `payment_with_note()`, the setting is passed to the transaction's `amt` field. The network fee is separate. Setting the variable to zero does not waive that fee. The amount applies to payment-note operations, including announcements and removal notices.

A source-code default or example is not evidence of the environment used during an experiment. Confirm the amount in original confirmed transaction records and, where available, the configuration used at the time. If historical records show a nonzero transfer, describe that separately from consumed network fees and reserved minimum balance. This package does not retrospectively change measurements or establish a zero historical amount.

## Repository documentation and configuration changes

- Updated the dissertation title and research scope in the README.
- Corrected CFF metadata: author affiliation and a preferred thesis citation.
- Retained software version 1.0.0 from the input; omitted the unverified release-date field. Record the actual release date when the version is published.
- Added installation steps for ports, file-based scripting, TEAL compilation, configuration, and functional checks.
- Added an explicit zero payment amount to both environment examples.
- Added `UMask=0022` to the portable service template for predictable cache readability; reference deployment units remain unchanged.
- Added version-control exclusions and expanded security notes.

## Validation scope

The repository review checked Python and JavaScript syntax, citation YAML structure, local documentation links, shell command syntax, and preservation of the original runtime files. It did not execute the full prototype, sign transactions, test openHAB, or reproduce all experimental measurements.

The original prototypes contain local trust assumptions and operational limitations. Publication cleanup is not a claim that the software has been hardened for arbitrary production deployments.
