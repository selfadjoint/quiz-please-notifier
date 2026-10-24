s = 'abcabcccabcabc'

max_length = 0
max_substring = ""
char_set = set()
left = 0

for right in range(len(s)):
    while s[right] in char_set:
        char_set.remove(s[left])
        left += 1

    char_set.add(s[right])
    if right - left + 1 > max_length:
        max_length = right - left + 1
        max_substring = s[left:right + 1]

print(f"Longest substring with unique characters: '{max_substring}' with length {max_length}")
