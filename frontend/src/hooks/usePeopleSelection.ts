import { useEntitySelection } from "./useEntitySelection";

export function usePeopleSelection() {
  return useEntitySelection<number>();
}
