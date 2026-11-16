export type UserStatus = "enabled" | "disabled";

export interface AuthenticatedUser {
  uid: string;
  username: string;
  display_name: string;
  email: string;
  status: UserStatus;
  create_time: string;
  last_login?: string | null;
  is_admin: boolean;
}
