-- chat_message_policy action_type에 'VIEWED' 추가
-- summary/policy_summary intent 처리 후 recent_policies 슬롯에 정책을 저장하기 위해 필요

ALTER TABLE chat_message_policy
    DROP CONSTRAINT IF EXISTS chat_message_policy_action_type_check;

ALTER TABLE chat_message_policy
    ADD CONSTRAINT chat_message_policy_action_type_check
        CHECK (action_type IN ('RECOMMENDED', 'COMPARED', 'ELIGIBILITY_TARGET', 'APPLY_TARGET', 'VIEWED'));
