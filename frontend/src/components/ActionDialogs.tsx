import ConfirmDialog from "./ConfirmDialog";

interface ActionDialogsProps {
  dialogType: "convert" | "deleteRecord" | "deleteFile" | null;
  loading?: boolean;
  onClose: () => void;
  onConfirmConvert: () => void;
  onConfirmDeleteRecord: () => void;
  onConfirmDeleteFile: () => void;
}

export function ActionDialogs({ dialogType, loading, onClose, onConfirmConvert, onConfirmDeleteRecord, onConfirmDeleteFile }: ActionDialogsProps) {
  const actions = {
    convert: { title: "Convert Video Format?", message: "Convert this video to a supported format.", label: "Convert", confirm: onConfirmConvert },
    deleteRecord: { title: "Remove from Library?", message: "The record will be removed from the database. The file on disk is kept and can be re-imported by scanning.", label: "Remove Record", confirm: onConfirmDeleteRecord },
    deleteFile: { title: "Delete File from Disk?", message: "The file will be permanently deleted from disk. This cannot be undone.", label: "Delete File", confirm: onConfirmDeleteFile },
  };
  const action = dialogType ? actions[dialogType] : null;
  return <ConfirmDialog open={action !== null} title={action?.title ?? ""} message={action?.message ?? ""}
    confirmLabel={action?.label} confirmColor={dialogType === "convert" ? "primary" : "error"}
    loading={loading} onClose={onClose} onConfirm={() => action?.confirm()} />;
}
