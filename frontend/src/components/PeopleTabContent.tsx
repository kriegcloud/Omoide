import React, { useState, useEffect } from "react";
import { PeopleSection } from "./PeopleSection";
import {
  runFaceCommand,
  assignFace,
  createPersonFromFaces,
  deleteFace,
  detachFace,
} from "../services/faceActions";
import { addMediaAppearanceToPerson } from "../services/personActions";
import { Person, Face } from "../types";

interface PeopleTabContentProps {
  mediaId: number;
  initialPersons: Person[];
  initialOrphans: Face[];
  onDataChanged: () => void;
}

export function PeopleTabContent({
  mediaId,
  initialPersons,
  initialOrphans,
  onDataChanged,
}: PeopleTabContentProps) {
  const [persons, setPersons] = useState(initialPersons);
  const [orphans, setOrphans] = useState(initialOrphans);

  useEffect(() => {
    setPersons(initialPersons);
    setOrphans(initialOrphans);
  }, [initialPersons, initialOrphans]);

  const handleDeleteFace = async (faceIds: number[]) => {
    const result = await runFaceCommand("Delete faces", faceIds, id => deleteFace(faceIds, "", id));
    setOrphans((prev) => prev.filter((f) => !(result.receipt?.applied ?? []).includes(`face:${f.id}`)));
    onDataChanged();
  };

  const handleAssignFace = async (faceIds: number[], personId: number) => {
    const result = await runFaceCommand("Assign faces", faceIds, id => assignFace(faceIds, personId, "manual", "", id));
    setOrphans((prev) => prev.filter((f) => !(result.receipt?.applied ?? []).includes(`face:${f.id}`)));
    onDataChanged();
  };

  const handleDetachFace = async (faceIds: number[]) => {
    await runFaceCommand("Detach faces", faceIds, id => detachFace(faceIds, "", id));
    onDataChanged();
  };

  const handleCreateFace = async (faceIds: number[], name?: string) => {
    const newPerson = await runFaceCommand("Create person", faceIds, id => createPersonFromFaces(faceIds, name, id));
    const result = newPerson as Person & { receipt?: { applied: string[] } };
    setOrphans((prev) => prev.filter((f) => !(result.receipt?.applied ?? []).includes(`face:${f.id}`)));
    onDataChanged();
    return newPerson;
  };
  const handleAttachMediaToPerson = async (personId: number) => {
    const result = await addMediaAppearanceToPerson(personId, mediaId);
    onDataChanged();
    return result;
  };

  return (
    <PeopleSection
      persons={persons}
      orphans={orphans}
      onAttachMediaToPerson={handleAttachMediaToPerson}
      onAssign={handleAssignFace}
      onDeleteFace={handleDeleteFace}
      onDetachFace={handleDetachFace}
      onCreateFace={handleCreateFace}
    />
  );
}
