"""Read optional IPS patches and apply emulator fixtures without writing ROMs."""


def read_ips(path):
    raw=path.read_bytes()
    assert raw[:5]==b'PATCH',path.name
    cursor=5
    records=[]
    while raw[cursor:cursor+3]!=b'EOF':
        assert cursor+5<=len(raw),path.name
        address=int.from_bytes(raw[cursor:cursor+3],'big')
        length=int.from_bytes(raw[cursor+3:cursor+5],'big')
        cursor+=5
        if length:
            data=raw[cursor:cursor+length]
            cursor+=length
        else:
            assert cursor+3<=len(raw),path.name
            length=int.from_bytes(raw[cursor:cursor+2],'big')
            data=raw[cursor+2:cursor+3]*length
            cursor+=3
        assert length and len(data)==length,path.name
        records.append((address,data))
    assert cursor+3==len(raw),'Optional patch must not truncate the ROM: '+path.name
    return records


def apply_ips(image,*patches):
    result=bytearray(image)
    for records in patches:
        for address,data in records:
            assert 0<=address and address+len(data)<=len(image),hex(address)
            result[address:address+len(data)]=data
    return bytes(result)
